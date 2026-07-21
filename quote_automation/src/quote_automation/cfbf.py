"""최소 CFBF(복합 문서, MS-CFB) 리더/라이터.

HWP 5.0 은 OLE 복합 문서(=CFBF) 컨테이너 안에 여러 스트림을 담는다.
``olefile`` 은 읽기 전용이고 스트림 크기를 바꿔 다시 쓸 수 없으므로,
스트림들을 통째로 받아 새 CFBF 파일로 직렬화하는 라이터를 직접 구현한다.

512바이트 섹터(major v3), 미니스트림(4096 미만) 지원.
디렉터리는 판독기(olefile / 한글)가 받아들이는 균형 이진트리로 구성한다.

이 모듈은 ``read_streams`` 로 원본을 읽어 ``write_cfbf`` 로 다시 쓴 뒤
모든 스트림이 바이트 단위로 동일한지 왕복 검증할 수 있다.
"""

from __future__ import annotations

import struct
from typing import Dict, List, Tuple

import olefile

# 특수 섹터 값
FREESECT = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE
FATSECT = 0xFFFFFFFD
DIFSECT = 0xFFFFFFFC
NOSTREAM = 0xFFFFFFFF

SECTOR = 512
MINISECTOR = 64
MINI_CUTOFF = 4096


def read_streams(path: str) -> List[Tuple[List[str], bytes]]:
    """CFBF 파일에서 (경로부분들, 데이터) 목록을 순서대로 읽는다."""
    ole = olefile.OleFileIO(path)
    out = []
    for entry in ole.listdir(streams=True, storages=False):
        data = ole.openstream(entry).read()
        out.append((list(entry), data))
    ole.close()
    return out


class _Dir:
    __slots__ = ("name", "typ", "left", "right", "child",
                 "start", "size", "color", "clsid")

    def __init__(self, name, typ):
        self.name = name
        self.typ = typ            # 1 storage, 2 stream, 5 root
        self.left = NOSTREAM
        self.right = NOSTREAM
        self.child = NOSTREAM
        self.start = ENDOFCHAIN
        self.size = 0
        self.color = 1            # black
        self.clsid = b"\x00" * 16


def _red_black_ish(indices: List[int]) -> Tuple[int, List[Tuple[int, int, int]]]:
    """정렬된 인덱스 목록으로 균형 BST 를 만들어 (루트, [(idx,left,right)]) 반환.

    CFBF 형제들은 이름 길이 → 대문자 이름 순으로 정렬된 트리로 저장된다.
    한글/olefile 은 색상(red/black)을 엄격히 검사하지 않으므로 균형 BST 로 충분하다.
    """
    links: List[Tuple[int, int, int]] = []

    def build(lo: int, hi: int) -> int:
        if lo > hi:
            return NOSTREAM
        mid = (lo + hi) // 2
        left = build(lo, mid - 1)
        right = build(mid + 1, hi)
        links.append((indices[mid], left, right))
        return indices[mid]

    root = build(0, len(indices) - 1)
    return root, links


def _cfbf_name_key(name: str) -> tuple:
    """CFBF 형제 정렬 키: (이름 길이, 대문자 이름)."""
    return (len(name), name.upper())


def write_cfbf(path: str, streams: List[Tuple[List[str], bytes]]) -> None:
    """스트림 목록을 새 CFBF 파일로 저장한다.

    streams: [(["BodyText","Section0"], b"..."), (["FileHeader"], b"..."), ...]
    루트 아래의 단일 계층 storage 구조(HWP 구조)만 지원한다.
    """
    # ---- 디렉터리 트리 구성 -------------------------------------------------
    entries: List[_Dir] = []
    root = _Dir("Root Entry", 5)
    entries.append(root)

    # 경로 -> 자식 이름 목록
    storages: Dict[str, List[str]] = {}          # storage 이름 -> [자식 stream 이름]
    top_children: List[str] = []                 # 루트 직계(스토리지 or 스트림) 이름
    stream_data: Dict[Tuple[str, ...], bytes] = {}

    for parts, data in streams:
        stream_data[tuple(parts)] = data
        if len(parts) == 1:
            if parts[0] not in top_children:
                top_children.append(parts[0])
        elif len(parts) == 2:
            sto, name = parts
            if sto not in top_children:
                top_children.append(sto)
                storages[sto] = []
            storages.setdefault(sto, [])
            if name not in storages[sto]:
                storages[sto].append(name)
        else:
            raise ValueError("2단계보다 깊은 스토리지는 지원하지 않습니다.")

    # 디렉터리 엔트리 생성 (인덱스 부여)
    name_to_idx: Dict[Tuple[str, ...], int] = {}
    for top in top_children:
        if top in storages:
            d = _Dir(top, 1)                     # storage
        else:
            d = _Dir(top, 2)                     # top-level stream
        entries.append(d)
        name_to_idx[(top,)] = len(entries) - 1
    for sto, children in storages.items():
        for name in children:
            d = _Dir(name, 2)
            entries.append(d)
            name_to_idx[(sto, name)] = len(entries) - 1

    # ---- 스트림 데이터 배치 (미니 / 일반) -----------------------------------
    mini_stream = bytearray()
    minifat: List[int] = []
    big_payloads: List[Tuple[int, bytes]] = []   # (dir_idx, data) for FAT streams

    def add_to_mini(data: bytes) -> int:
        start = len(minifat)
        n = max(1, (len(data) + MINISECTOR - 1) // MINISECTOR)
        for k in range(n):
            mini_stream.extend(data[k * MINISECTOR:(k + 1) * MINISECTOR])
            # 마지막 미니섹터 패딩
            if len(mini_stream) % MINISECTOR:
                mini_stream.extend(b"\x00" * (MINISECTOR - len(mini_stream) % MINISECTOR))
            minifat.append(len(minifat) + 1)
        minifat[-1] = ENDOFCHAIN
        return start

    for (parts_key, idx) in name_to_idx.items():
        data = stream_data.get(parts_key)
        if data is None:                          # storage
            continue
        d = entries[idx]
        d.size = len(data)
        if len(data) < MINI_CUTOFF:
            d.start = add_to_mini(data) if data else ENDOFCHAIN
        else:
            big_payloads.append((idx, data))

    # ---- 트리 링크 구성 -----------------------------------------------------
    # 루트의 child = top-level 형제 트리
    top_idx_sorted = sorted(
        (name_to_idx[(t,)] for t in top_children),
        key=lambda i: _cfbf_name_key(entries[i].name),
    )
    if top_idx_sorted:
        r, links = _red_black_ish(top_idx_sorted)
        root.child = r
        for idx, l, rr in links:
            entries[idx].left = l
            entries[idx].right = rr
    for sto, children in storages.items():
        child_idx_sorted = sorted(
            (name_to_idx[(sto, c)] for c in children),
            key=lambda i: _cfbf_name_key(entries[i].name),
        )
        r, links = _red_black_ish(child_idx_sorted)
        entries[name_to_idx[(sto,)]].child = r
        for idx, l, rr in links:
            entries[idx].left = l
            entries[idx].right = rr

    # ---- 섹터 배치 계획 -----------------------------------------------------
    # 순서: [일반 스트림 섹터들][미니스트림 섹터들][miniFAT 섹터들][디렉터리 섹터들]
    # FAT 는 위 전체 체인을 기술한다. DIFAT 은 헤더 109칸에 들어간다고 가정(작은 파일).
    def nsect(nbytes: int) -> int:
        return (nbytes + SECTOR - 1) // SECTOR

    layout: List[Tuple[str, int]] = []           # (kind, dir_idx or -1) per sector, for chains
    chains: Dict[str, List[int]] = {}

    next_sector = 0

    def alloc(nbytes: int, key: str) -> int:
        nonlocal next_sector
        cnt = max(1, nsect(nbytes))
        start = next_sector
        chains[key] = list(range(start, start + cnt))
        next_sector += cnt
        return start

    # 일반 스트림들
    for idx, data in big_payloads:
        entries[idx].start = alloc(len(data), f"big{idx}")

    # 미니스트림 (루트 엔트리의 스트림)
    mini_bytes = bytes(mini_stream)
    if mini_bytes:
        root.start = alloc(len(mini_bytes), "mini")
        root.size = len(mini_bytes)
    else:
        root.start = ENDOFCHAIN
        root.size = 0

    # miniFAT 섹터
    minifat_bytes = b"".join(struct.pack("<I", v) for v in minifat)
    minifat_bytes += b"\xff" * ((-len(minifat_bytes)) % SECTOR)
    n_minifat_sect = nsect(len(minifat_bytes)) if minifat else 0
    first_minifat = alloc(len(minifat_bytes), "minifat") if n_minifat_sect else ENDOFCHAIN

    # 디렉터리 섹터
    dir_bytes = _serialize_directory(entries)
    dir_bytes += b"\x00" * ((-len(dir_bytes)) % SECTOR)
    first_dir = alloc(len(dir_bytes), "dir")

    total_content_sectors = next_sector

    # ---- FAT 구성 -----------------------------------------------------------
    # FAT 자체가 차지할 섹터 수를 반복 계산 (FAT 는 모든 섹터를 기술)
    def compute_fat_sectors(content: int) -> int:
        prev = 0
        while True:
            total = content + prev
            need = nsect(total * 4)
            if need == prev:
                return need
            prev = need

    n_fat_sect = compute_fat_sectors(total_content_sectors)
    fat_sectors = list(range(total_content_sectors, total_content_sectors + n_fat_sect))
    total_sectors = total_content_sectors + n_fat_sect

    fat = [FREESECT] * total_sectors
    # 체인들
    for key, secs in chains.items():
        for a, b in zip(secs, secs[1:]):
            fat[a] = b
        fat[secs[-1]] = ENDOFCHAIN
    for s in fat_sectors:
        fat[s] = FATSECT

    fat_bytes = b"".join(struct.pack("<I", v) for v in fat)
    fat_bytes += struct.pack("<I", FREESECT) * ((-len(fat) * 4 // 4) % (SECTOR // 4))
    # pad FAT to full sectors
    if len(fat_bytes) % SECTOR:
        fat_bytes += struct.pack("<I", FREESECT) * ((SECTOR - len(fat_bytes) % SECTOR) // 4)

    # ---- 헤더 ---------------------------------------------------------------
    header = bytearray(512)
    header[0:8] = bytes.fromhex("D0CF11E0A1B11AE1")
    header[8:24] = b"\x00" * 16                    # CLSID
    struct.pack_into("<H", header, 24, 0x003E)     # minor
    struct.pack_into("<H", header, 26, 0x0003)     # major (512 sectors)
    struct.pack_into("<H", header, 28, 0xFFFE)     # byte order
    struct.pack_into("<H", header, 30, 0x0009)     # sector shift (512)
    struct.pack_into("<H", header, 32, 0x0006)     # mini sector shift (64)
    struct.pack_into("<H", header, 34, 0)          # reserved
    struct.pack_into("<I", header, 36, 0)          # reserved2
    struct.pack_into("<I", header, 40, 0)          # num dir sectors (0 for v3)
    struct.pack_into("<I", header, 44, n_fat_sect)
    struct.pack_into("<I", header, 48, first_dir)
    struct.pack_into("<I", header, 52, 0)          # transaction sig
    struct.pack_into("<I", header, 56, MINI_CUTOFF)
    struct.pack_into("<I", header, 60, first_minifat)
    struct.pack_into("<I", header, 64, n_minifat_sect)
    struct.pack_into("<I", header, 68, ENDOFCHAIN)  # first DIFAT sector
    struct.pack_into("<I", header, 72, 0)           # num DIFAT sectors
    # DIFAT 배열 (109칸)
    difat = fat_sectors + [FREESECT] * (109 - len(fat_sectors))
    if len(fat_sectors) > 109:
        raise ValueError("FAT 섹터가 109개를 초과 — DIFAT 확장 미구현 (파일이 너무 큼)")
    for k in range(109):
        struct.pack_into("<I", header, 76 + 4 * k, difat[k])

    # ---- 섹터 본문 조립 -----------------------------------------------------
    body = bytearray()

    def put(key: str, raw: bytes):
        raw = raw + b"\x00" * ((-len(raw)) % SECTOR)
        assert len(raw) // SECTOR == len(chains[key]), (key, len(raw)//SECTOR, len(chains[key]))
        body.extend(raw)

    for idx, data in big_payloads:
        put(f"big{idx}", data)
    if mini_bytes:
        put("mini", mini_bytes)
    if n_minifat_sect:
        put("minifat", minifat_bytes)
    put("dir", dir_bytes)
    body.extend(fat_bytes)

    with open(path, "wb") as f:
        f.write(header)
        f.write(body)


def _serialize_directory(entries: List[_Dir]) -> bytes:
    out = bytearray()
    for d in entries:
        e = bytearray(128)
        nm = d.name.encode("utf-16-le")[:62]
        e[0:len(nm)] = nm
        struct.pack_into("<H", e, 64, len(nm) + 2)   # name length incl null
        e[66] = d.typ
        e[67] = d.color
        struct.pack_into("<I", e, 68, d.left)
        struct.pack_into("<I", e, 72, d.right)
        struct.pack_into("<I", e, 76, d.child)
        e[80:96] = d.clsid
        struct.pack_into("<I", e, 96, 0)             # state bits
        e[100:116] = b"\x00" * 16                    # times
        struct.pack_into("<I", e, 116, d.start)
        struct.pack_into("<Q", e, 120, d.size)
        out.extend(e)
    return bytes(out)
