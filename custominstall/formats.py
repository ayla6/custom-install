# This file is a part of custom-install.
#
# custom-install is copyright (c) 2019 Ian Burgwin
# This file is licensed under The MIT License (MIT).
# You can find the full license text in LICENSE.md in the root of this project.

"""Support for installing titles that are not CIAs.

This covers CCI files (.cci/.3ds cart dumps), standalone NCCH files (.cxi/.app)
and their Z3DS-compressed versions (.zcci/.zcxi/.zcia), as used by Azahar.

Since a cart dump does not carry the certificate/ticket/TMD structure of a CIA,
these are installed the same way tools like GodMode9 and rom-converto do when
converting a dump to CIA:

* each NCCH partition (cart partitions 0-2, the whole file for a CXI) becomes
  one unencrypted CIA content, stored verbatim
* the exheader of the first partition gets the SD application flag set, and the
  exheader hash stored in the NCCH header is updated to match
* a synthetic TMD is built around the contents, with a blank signature and a
  zeroed title key

The synthetic TMD is valid enough for custom-install and custom-install-finalize
on CFW. The certificate chain and ticket are not synthesized at all, since the
SD install process here never reads them; finalize.3dsx creates the ticket on
the console itself.
"""

from hashlib import sha256
from os import dup, fdopen
from os.path import isdir, join
from tempfile import TemporaryFile

import zstandard

from pyctr.crypto import MissingSeedError
from pyctr.type.cci import CCIReader, CCISection, CCIError
from pyctr.type.cdn import CDNReader
from pyctr.type.cia import CIAReader, CIAError
from pyctr.type.ncch import NCCHReader, NCCHSection, NCCHError, NCCHSeedError
from pyctr.type.tmd import ContentChunkRecord, ContentInfoRecord, ContentTypeFlags, TitleMetadataReader, TitleVersion

READ_SIZE = 0x400000

Z3DS_MAGIC = b'Z3DS'

# content type flags with every flag unset, for the unencrypted synthetic contents
_UNENCRYPTED = ContentTypeFlags(False, False, False, False, False)

# RSA-2048-SHA-256 with a zeroed signature body. Every CCI to CIA converter writes
# the TMD signature like this; the console treats an all-zero signature as
# "unsigned" and skips verification, but a non-zero one (such as pyctr's
# BLANK_SIG_PAIR of 0xFF bytes) gets verified and rejected, breaking the title.
_BLANK_SIGNATURE = (0x00010004, b'\x00' * 0x100)

# inner magics allowed inside a Z3DS file, mapped to what they are handled as
_Z3DS_UNDERLYING = {b'NCSD': 'cci', b'NCCH': 'ncch', b'CIA\x00': 'cia'}


class UnsupportedFormatError(Exception):
    """The file is a known format that cannot be installed, or is corrupt."""


class Z3DSFileHeader:
    """Header of the Z3DS container format used by Azahar (0x20 bytes, little-endian)."""

    def __init__(self, raw: bytes):
        if raw[:4] != Z3DS_MAGIC:
            raise UnsupportedFormatError('Z3DS magic not found')
        self.underlying_magic = raw[0x4:0x8]
        self.version = raw[0x8]
        self.header_size = int.from_bytes(raw[0xA:0xC], 'little')
        self.metadata_size = int.from_bytes(raw[0xC:0x10], 'little')
        self.compressed_size = int.from_bytes(raw[0x10:0x18], 'little')
        self.uncompressed_size = int.from_bytes(raw[0x18:0x20], 'little')

    @property
    def data_offset(self) -> int:
        """Offset of the zstd stream, which starts after the header and metadata."""
        return self.header_size + self.metadata_size


def sniff_format(f) -> 'str | None':
    """Sniff a seekable file object for a supported title format.

    Returns 'cia', 'cci', 'ncch', 'z3ds', or None if unrecognized.
    The file position is restored before returning.
    """
    f.seek(0)
    head = f.read(0x104)
    f.seek(0)
    if len(head) < 0x104:
        return None
    if head[:4] == Z3DS_MAGIC:
        return 'z3ds'
    if int.from_bytes(head[:4], 'little') == 0x2020:
        return 'cia'
    if head[0x100:0x104] == b'NCSD':
        return 'cci'
    if head[0x100:0x104] == b'NCCH':
        return 'ncch'
    return None


def decompress_z3ds(f, out):
    """Decompress a Z3DS file (a seekable zstd stream) into a file object.

    The stream is a series of zstd frames followed by a seek table. Decompression
    stops once the size stored in the header is reached, so the seek table is
    never touched.
    """
    header = Z3DSFileHeader(f.read(0x20))
    if header.version != 1:
        raise UnsupportedFormatError(f'unsupported Z3DS version {header.version}')
    if header.header_size < 0x20:
        raise UnsupportedFormatError('Z3DS header size is too small')

    underlying = _Z3DS_UNDERLYING.get(header.underlying_magic)
    if underlying is None:
        raise UnsupportedFormatError(f'unsupported Z3DS content type {header.underlying_magic!r}')

    f.seek(header.data_offset)
    dctx = zstandard.ZstdDecompressor()
    obj = dctx.decompressobj()
    total = 0
    pending = b''
    while total < header.uncompressed_size:
        if pending:
            chunk, pending = pending, b''
        else:
            chunk = f.read(READ_SIZE)
            if not chunk:
                break
        try:
            data = obj.decompress(chunk)
        except zstandard.ZstdError as e:
            raise UnsupportedFormatError(f'corrupt Z3DS file: {e}') from e
        if data:
            if total + len(data) > header.uncompressed_size:
                data = data[:header.uncompressed_size - total]
            out.write(data)
            total += len(data)
        if obj.eof:
            # hold any bytes after this frame (start of the next one) for the next pass
            pending = obj.unused_data
            obj = dctx.decompressobj()

    if total != header.uncompressed_size:
        raise UnsupportedFormatError('Z3DS file is truncated')
    return underlying


class _PatchedSectionIO:
    """Read-only seekable view of a file object with fixed regions replaced.

    Reads are exact-length until end of stream, matching SubsectionIO, since
    the install code does not handle short reads.
    """

    def __init__(self, base, size: int, patches: 'list[tuple[int, bytes]]'):
        self._base = base
        self._size = size
        self._patches = sorted(patches)
        self._pos = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self._pos = offset
        elif whence == 1:
            self._pos += offset
        elif whence == 2:
            self._pos = self._size + offset
        else:
            raise ValueError(f'invalid whence ({whence}, should be 0, 1 or 2)')
        self._pos = max(0, min(self._pos, self._size))
        return self._pos

    def tell(self) -> int:
        return self._pos

    def read(self, size: int = -1) -> bytes:
        if size == -1 or self._pos + size > self._size:
            size = self._size - self._pos
        if size <= 0:
            return b''
        out = bytearray()
        pos = self._pos
        end = self._pos + size
        while pos < end:
            patch = next(((o, d) for o, d in self._patches if o <= pos < o + len(d)), None)
            if patch:
                offset, data = patch
                take = min(end, offset + len(data)) - pos
                out += data[pos - offset:pos - offset + take]
                pos += take
            else:
                # read raw up to the next patch, or the end of the request
                next_patch = min((o for o, _ in self._patches if o > pos), default=end)
                take = min(end, next_patch) - pos
                self._base.seek(pos)
                raw = self._base.read(take)
                out += raw
                pos += len(raw)
                if len(raw) < take:
                    # underlying stream ended early
                    break
        self._pos = pos
        return bytes(out)

    def close(self):
        self._base.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _patched_prefix(ncch: NCCHReader) -> 'tuple[list[tuple[int, bytes]], int]':
    """Build the patches that set the SD application flag on a partition's exheader.

    This is what CCI to CIA converters change so a cart dump can be installed as
    a digital title: the flag in the exheader itself, and the exheader hash kept
    in the NCCH header.

    Returns a list of (offset, data) patches relative to the partition start,
    plus the save data size from the exheader storage info.
    """
    with ncch.open_raw_section(NCCHSection.ExtendedHeader) as e:
        exheader = bytearray(e.read())

    # SD application flag
    exheader[0xD] |= 0x2

    # savedata size for the synthetic TMD
    save_size = int.from_bytes(exheader[0x1C0:0x1C4], 'little')

    exheader_size = int.from_bytes(ncch.get_data(NCCHSection.Header, 0x180, 4), 'little')
    exheader_size = min(exheader_size, len(exheader))

    # the NCCH header is never encrypted, so the new hash goes in as-is
    new_hash = sha256(bytes(exheader[:exheader_size])).digest()
    patches = [(0x160, new_hash)]

    if ncch.flags.no_crypto:
        patches.append((0x200, bytes(exheader)))
    else:
        # re-encrypt the patched exheader with the key the partition already uses
        crypto = ncch._crypto
        keyslot = ncch.main_keyslot
        region = ncch.sections[NCCHSection.ExtendedHeader]
        cipher = crypto.create_ctr_cipher(keyslot, region.iv)
        patches.append((0x200, cipher.encrypt(bytes(exheader))))

    return patches, save_size


class TitleReader:
    """Presents CCI cart dumps and standalone NCCH files as if they were CIAs.

    Only the parts of :class:`~pyctr.type.cia.CIAReader` used by custom-install
    are implemented.
    """

    def __init__(self, title_id: str, contents: 'dict[int, NCCHReader]', sizes: 'dict[int, int]',
                 open_partition: 'callable'):
        self.title_id = title_id.lower()
        """Title ID, available without hashing the contents."""
        self.contents = contents
        self._sizes = sizes
        self._open_partition = open_partition
        self._tmd = None

        self._patches = {}
        self._save_size = 0
        first = contents.get(0)
        if first is not None and first.check_for_extheader():
            self._patches[0], self._save_size = _patched_prefix(first)

    @classmethod
    def from_cci(cls, cci: CCIReader) -> 'TitleReader':
        """Use partitions 0-2 (application, manual, download play) of a CCI.

        The other partitions (update data and unused slots) are dropped, like
        other CCI to CIA converters do.
        """
        if CCISection.Application not in cci.sections:
            raise UnsupportedFormatError('CCI has no application partition (partition 0)')

        contents = {}
        sizes = {}
        for section in (CCISection.Application, CCISection.Manual, CCISection.DownloadPlayChild):
            region = cci.sections.get(section)
            if region is None:
                continue
            ncch = cci.contents.get(section)
            if ncch is None:
                ncch = NCCHReader(cci.open_raw_section(section))
            contents[int(section)] = ncch
            sizes[int(section)] = region.size

        return cls(cci.media_id, contents, sizes, lambda cindex: cci.open_raw_section(CCISection(cindex)))

    @classmethod
    def from_ncch(cls, ncch: NCCHReader, open_stream: 'callable') -> 'TitleReader':
        """Use a standalone NCCH file (.cxi/.app) as the only content."""
        with open_stream() as f:
            f.seek(0, 2)
            size = f.tell()
        return cls(ncch.program_id, {0: ncch}, {0: size}, lambda cindex: open_stream())

    @property
    def tmd(self) -> TitleMetadataReader:
        """A synthetic TMD. Built on first use, since content hashes are needed."""
        if self._tmd is None:
            records = []
            for cindex in sorted(self._sizes):
                content_hash = sha256()
                with self.open_raw_section(cindex) as s:
                    left = self._sizes[cindex]
                    while left > 0:
                        data = s.read(min(READ_SIZE, left))
                        if not data:
                            raise UnsupportedFormatError(f'content {cindex} is truncated')
                        content_hash.update(data)
                        left -= len(data)
                records.append(ContentChunkRecord(id=f'{cindex:08x}', cindex=cindex,
                                                  type=_UNENCRYPTED,
                                                  size=self._sizes[cindex], hash=content_hash.digest()))

            chunk_records_raw = b''.join(bytes(r) for r in records)
            info_records = [ContentInfoRecord(index_offset=0, command_count=len(records),
                                              hash=sha256(chunk_records_raw).digest())]
            self._tmd = TitleMetadataReader(signature=_BLANK_SIGNATURE,
                                            title_id=self.title_id, save_size=self._save_size, srl_save_size=0,
                                            title_version=TitleVersion.from_int(0), info_records=info_records,
                                            chunk_records=records)
        return self._tmd

    @property
    def content_info(self) -> 'list[ContentChunkRecord]':
        return list(self.tmd.chunk_records)

    def open_raw_section(self, cindex: int):
        base = self._open_partition(cindex)
        patches = self._patches.get(cindex)
        if patches:
            return _PatchedSectionIO(base, self._sizes[cindex], patches)
        return base


def _dup_stream(f) -> 'BinaryIO':
    """Create an independent stream for the same file, without reopening by name."""
    return fdopen(dup(f.fileno()), 'rb')


def _reader_from_file(f):
    """Build a reader from an open, seekable file object."""
    kind = sniff_format(f)
    if kind == 'z3ds':
        # the inner file needs random access, so decompress to a temporary file
        tmp = TemporaryFile()
        with f:
            decompress_z3ds(f, tmp)
        return _reader_from_file(tmp)
    if kind == 'cia':
        return CIAReader(f)
    if kind == 'cci':
        return TitleReader.from_cci(CCIReader(f))
    if kind == 'ncch':
        ncch = NCCHReader(f)
        return TitleReader.from_ncch(ncch, lambda: _dup_stream(f))
    raise UnsupportedFormatError('not a supported title format')


def get_reader(path: 'Union[PathLike, bytes, str]'):
    """Read a title from a path, given any supported format.

    Raises UnsupportedFormatError for known-but-unusable formats, and the pyctr
    errors (CIAError, CDNError, ...) for corrupt files, like the CIA path does.
    """
    if isdir(path):
        return CDNReader(join(path, 'tmd'))

    with open(path, 'rb') as f:
        kind = sniff_format(f)

    if kind == 'z3ds':
        return _reader_from_file(open(path, 'rb'))

    if kind == 'cci':
        try:
            cci = CCIReader(path)
        except (MissingSeedError, NCCHSeedError):
            # let seed errors through untouched so the caller can show a useful message
            raise
        except (CCIError, NCCHError) as e:
            raise UnsupportedFormatError(f'could not read CCI: {e}') from e
        return TitleReader.from_cci(cci)

    if kind == 'ncch':
        try:
            ncch = NCCHReader(open(path, 'rb'))
        except (MissingSeedError, NCCHSeedError):
            raise
        except NCCHError as e:
            raise UnsupportedFormatError(f'could not read NCCH: {e}') from e
        return TitleReader.from_ncch(ncch, lambda: open(path, 'rb'))

    # CIA, or something unrecognized that may be a TMD file
    try:
        return CIAReader(path)
    except CIAError:
        # if there was an error with parsing the CIA header,
        # the file would be tried in CDNReader next (assuming it's a tmd)
        # any other error should be propagated to the caller
        return CDNReader(path)
