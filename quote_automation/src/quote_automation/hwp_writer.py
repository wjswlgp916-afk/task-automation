"""견적서 HWP 렌더러.

원본 한글 양식(quote_template.hwp)을 템플릿으로, 최소·안전한 편집만으로
새 .hwp 를 만든다.

전략
----
* 품목 테이블의 **데이터 행은 원본 행을 그대로 선택·보존**한다.
  (셀 안의 여러 문단, 누름틀 필드까지 바이트 단위로 유지 → 가장 안전)
* 필요 없는 행은 삭제하고, 남은 행을 0..N-1 로 **재번호**한다.
* 오직 전역 셀(발급일자 · 대학명 · 합계금액 · 합 계)만 순수 텍스트로 교체한다.
* 편집 후 **다시 파싱해 표 불변식(행 수 · 열 타일링)을 검증**한다.

한글로 직접 열어볼 수 없는 환경이므로, 구조 불변식 검증으로 무결성을 보장한다.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import cfbf
from .engine import Quote

# HWP 레코드 태그
PARA_HEADER = 66
PARA_TEXT = 67
PARA_CHAR_SHAPE = 68
PARA_LINE_SEG = 69
CTRL_HEADER = 71
LIST_HEADER = 72
TABLE = 77
MEMO_LIST = 93

PARA_BREAK = 0x000D          # 문단 끝 제어문자


@dataclass
class Record:
    tag: int
    level: int
    payload: bytes

    def serialize(self) -> bytes:
        size = len(self.payload)
        if size < 0xFFF:
            header = (self.tag & 0x3FF) | ((self.level & 0x3FF) << 10) | (size << 20)
            return struct.pack("<I", header) + self.payload
        header = (self.tag & 0x3FF) | ((self.level & 0x3FF) << 10) | (0xFFF << 20)
        return struct.pack("<I", header) + struct.pack("<I", size) + self.payload


def parse_records(data: bytes) -> List[Record]:
    out: List[Record] = []
    i = 0
    n = len(data)
    while i < n:
        (h,) = struct.unpack("<I", data[i:i + 4])
        i += 4
        tag = h & 0x3FF
        level = (h >> 10) & 0x3FF
        size = (h >> 20) & 0xFFF
        if size == 0xFFF:
            (size,) = struct.unpack("<I", data[i:i + 4])
            i += 4
        out.append(Record(tag, level, data[i:i + size]))
        i += size
    return out


def serialize_records(records: List[Record]) -> bytes:
    return b"".join(r.serialize() for r in records)


# --------------------------------------------------------------------------- #
# 문단 텍스트 도우미
# --------------------------------------------------------------------------- #
# 8 WCHAR 를 차지하는 인라인 컨트롤 문자 코드 (누름틀 등 필드 begin/end 포함)
_EIGHT_WIDE_CTRL = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 21, 22, 23}


def text_of(rec: Record) -> str:
    """PARA_TEXT 레코드에서 사람이 읽는 텍스트만 추출한다.

    HWP 인라인 컨트롤(누름틀/필드 등)은 8 WCHAR 블록을 차지하므로 통째로 건너뛴다.
    """
    u = struct.unpack(f"<{len(rec.payload) // 2}H", rec.payload)
    out = []
    i = 0
    n = len(u)
    while i < n:
        c = u[i]
        if c in _EIGHT_WIDE_CTRL:
            i += 8
        elif c < 32 or c >= 0xFFF0:
            i += 1
        else:
            out.append(chr(c))
            i += 1
    return "".join(out)


def _units(rec: Record) -> Tuple[int, ...]:
    return struct.unpack(f"<{len(rec.payload) // 2}H", rec.payload)


def set_plain_text(para_hdr: Record, para_txt: Record, new_text: str) -> None:
    """문단을 순수 텍스트로 교체한다 (끝 문단부호 0x0D 유지, 글자수 갱신)."""
    old = _units(para_txt)
    trailing = old and old[-1] == PARA_BREAK
    units = [ord(c) for c in new_text]
    if trailing:
        units.append(PARA_BREAK)
    para_txt.payload = b"".join(struct.pack("<H", u) for u in units)

    # PARA_HEADER 의 글자 수(하위 31비트)만 갱신, 상위 플래그는 보존
    (nchars,) = struct.unpack("<I", para_hdr.payload[0:4])
    nchars = (nchars & 0x80000000) | (len(units) & 0x7FFFFFFF)
    para_hdr.payload = struct.pack("<I", nchars) + para_hdr.payload[4:]


def _adjust_para_header_length(para_hdr: Record, delta: int) -> None:
    (nchars,) = struct.unpack("<I", para_hdr.payload[0:4])
    new_len = (nchars & 0x7FFFFFFF) + delta
    nchars = (nchars & 0x80000000) | (new_len & 0x7FFFFFFF)
    para_hdr.payload = struct.pack("<I", nchars) + para_hdr.payload[4:]


def replace_literal(para_hdr: Record, para_txt: Record, old: str, new: str) -> bool:
    """문단 안에서 순수 텍스트 ``old`` 부분만 찾아 ``new`` 로 치환한다.

    라벨과 값이 한 문장에 섞인 경우(예: '청구금액 : 금 O백O십O만원 (\\\\ 0,000,000 )')
    처럼 문단 전체가 아니라 **일부만** 바꿔야 할 때 쓴다. ``old`` 가 없으면
    아무것도 하지 않고 False 를 반환한다.

    ``old`` 는 인라인 컨트롤(누름틀 등)에 끊기지 않는 순수 텍스트 구간이어야
    한다 — 원시 코드유닛을 그대로 대조하므로, 대상 구간에 제어문자가
    섞여 있으면 찾지 못한다 (본문 문장 치환에는 보통 해당 없음).
    """
    units = list(_units(para_txt))
    old_units = [ord(c) for c in old]
    n = len(old_units)
    for i in range(len(units) - n + 1):
        if units[i:i + n] == old_units:
            new_units = units[:i] + [ord(c) for c in new] + units[i + n:]
            para_txt.payload = b"".join(struct.pack("<H", u) for u in new_units)
            _adjust_para_header_length(para_hdr, len(new_units) - len(units))
            return True
    return False


def replace_literal_everywhere(records: List[Record], old: str, new: str) -> int:
    """문서 전체 문단을 훑어 ``old`` 를 ``new`` 로 치환한다. 치환 횟수를 반환."""
    count = 0
    for i, r in enumerate(records):
        if r.tag == PARA_TEXT:
            hdr = _prev_para_header(records, i)
            if hdr and replace_literal(hdr, r, old, new):
                count += 1
    return count


# 한글 "메모(코멘트)" 컨트롤의 4바이트 식별자. 메모는 해당 문단을 필드처럼
# 감싸는 형태로 저장되며, 이 CTRL_HEADER 안에 "MEMO/.../작성자/..." 식
# 식별 문자열과 작성자·타임스탬프가 그대로 들어있다.
_MEMO_CTRL_ID = b"knu%"


def strip_memo_controls(records: List[Record]) -> int:
    """문서에 남아있는 한글 메모(코멘트)를 찾아 제거한다.

    원본 양식을 만들 때 검토용으로 남긴 메모가 지워지지 않은 채 템플릿에
    섞여 있으면, 그 메모가 걸린 문단을 포함하는 모든 산출물에 매번
    따라오고 — 한글에서 "메모를 읽는 중 오류" 경고까지 띄운다.

    메모는 두 부분으로 이루어져 있다.
      1. 본문 문단을 필드(누름틀)처럼 감싸는 CTRL_HEADER(id="knu%") — 문단을
         순수 텍스트로 정리(제어문자 제거)하고 이 레코드 자체를 삭제한다.
      2. 문서 끝쪽에 별도로 붙는 메모 내용 자체(MEMO_LIST 레코드 + 그
         안의 LIST_HEADER/문단들, 즉 메모창에 보이는 노란 메모 텍스트) —
         이것도 통째로 삭제해야 한다. 1번만 지우고 이걸 남겨두면, 앵커는
         없는데 메모 목록만 남는 불일치 상태가 되어 여전히 같은 경고가 뜬다.

    제거한 메모(앵커) 개수를 반환한다 (없으면 0, 안전하게 아무 일도 하지 않음).
    """
    remove_idx: List[int] = []
    for i, r in enumerate(records):
        if r.tag == CTRL_HEADER and r.payload[:4] == _MEMO_CTRL_ID:
            remove_idx.append(i)
            for j in range(i - 1, -1, -1):
                if records[j].tag == PARA_TEXT:
                    hdr = _prev_para_header(records, j)
                    if hdr:
                        clean = text_of(records[j])
                        set_plain_text(hdr, records[j], clean)
                        # 필드 마스크 제거(순수 텍스트가 됐으므로)
                        hdr.payload = hdr.payload[0:4] + b"\x00\x00\x00\x00" + hdr.payload[8:]
                    break
                if records[j].tag == PARA_HEADER:
                    break
    removed = len(remove_idx)
    for i in sorted(remove_idx, reverse=True):
        del records[i]

    # MEMO_LIST 블록(메모 내용) 통째 제거.
    kept: List[Record] = []
    i = 0
    n = len(records)
    while i < n:
        r = records[i]
        if r.tag == MEMO_LIST:
            base_level = r.level
            j = i + 1
            while j < n and records[j].tag != MEMO_LIST and records[j].level >= base_level:
                j += 1
            i = j
            continue
        kept.append(r)
        i += 1
    records[:] = kept
    return removed


# --------------------------------------------------------------------------- #
# 셀 / 행 모델
# --------------------------------------------------------------------------- #
@dataclass
class Cell:
    records: List[Record]            # LIST_HEADER + 문단들 (+ 컨트롤)

    @property
    def header(self) -> Record:
        return self.records[0]

    def col(self) -> int:
        return struct.unpack("<H", self.header.payload[8:10])[0]

    def row(self) -> int:
        return struct.unpack("<H", self.header.payload[10:12])[0]

    def colspan(self) -> int:
        return struct.unpack("<H", self.header.payload[12:14])[0]

    def rowspan(self) -> int:
        return struct.unpack("<H", self.header.payload[14:16])[0]

    def set_row(self, r: int) -> None:
        p = bytearray(self.header.payload)
        struct.pack_into("<H", p, 10, r)
        self.header.payload = bytes(p)

    def set_rowspan(self, n: int) -> None:
        """세로 병합 칸 수를 바꾼다 (검수확인서처럼 rowspan 이 있는 표에서,
        일부 하위 행을 지운 뒤 상위 라벨 칸의 병합 수를 실제 행 수에 맞춘다)."""
        p = bytearray(self.header.payload)
        struct.pack_into("<H", p, 14, n)
        self.header.payload = bytes(p)

    def paragraphs(self) -> List[Tuple[Record, Record]]:
        """셀 안의 (PARA_HEADER, PARA_TEXT) 쌍 목록."""
        pairs = []
        i = 1
        recs = self.records
        while i < len(recs):
            if recs[i].tag == PARA_HEADER:
                txt = None
                j = i + 1
                while j < len(recs) and recs[j].tag != PARA_HEADER:
                    if recs[j].tag == PARA_TEXT:
                        txt = recs[j]
                        break
                    j += 1
                if txt is not None:
                    pairs.append((recs[i], txt))
            i += 1
        return pairs

    def first_text(self) -> str:
        for _, txt in self.paragraphs():
            t = text_of(txt)
            if t.strip():
                return t.strip()
        return ""

    def set_single_text(self, new_text: str) -> None:
        """셀을 '하나의 순수 텍스트 문단'으로 만든다 (누름틀·부가 컨트롤 제거)."""
        pairs = self.paragraphs()
        if not pairs:
            return
        hdr, txt = pairs[0]
        set_plain_text(hdr, txt, new_text)
        # 누름틀/필드를 제거했으므로 문단 컨트롤 마스크([4:8])를 0 으로 정리
        hdr.payload = hdr.payload[0:4] + b"\x00\x00\x00\x00" + hdr.payload[8:]
        # 첫 문단만 남기고, 이 문단에 딸린 부가 컨트롤(CTRL_HEADER 등)과
        # 나머지 문단은 제거해 일관성을 유지한다.
        keep = [self.records[0], hdr, txt]
        # 첫 문단의 CHAR_SHAPE / LINE_SEG 는 보존
        idx = self.records.index(txt) + 1
        while idx < len(self.records) and self.records[idx].tag in (
            PARA_CHAR_SHAPE, PARA_LINE_SEG
        ):
            keep.append(self.records[idx])
            idx += 1
        # LIST_HEADER 의 문단 수를 1 로 설정
        p = bytearray(self.records[0].payload)
        struct.pack_into("<h", p, 0, 1)
        self.records[0].payload = bytes(p)
        self.records = keep

    def clone(self) -> "Cell":
        return Cell([Record(r.tag, r.level, r.payload) for r in self.records])


def _split_cells(records: List[Record], table_level: int) -> List[Cell]:
    """테이블 레코드 뒤에 오는 셀(LIST_HEADER 단위)들을 분리한다."""
    cells: List[Cell] = []
    cur: List[Record] = []
    for r in records:
        if r.tag == LIST_HEADER and r.level == table_level:
            if cur:
                cells.append(Cell(cur))
            cur = [r]
        else:
            cur.append(r)
    if cur:
        cells.append(Cell(cur))
    return cells


# --------------------------------------------------------------------------- #
# 템플릿 로딩 / 표 위치 파악
# --------------------------------------------------------------------------- #
def _default_template() -> Path:
    return Path(__file__).parent / "templates" / "quote_template.hwp"


def _read_section(streams) -> Tuple[List[Record], int, dict]:
    """스트림 목록에서 BodyText/Section0 을 찾아 레코드로 파싱한다."""
    sm = {tuple(p): d for p, d in streams}
    fileheader = sm[("FileHeader",)]
    (flags,) = struct.unpack("<I", fileheader[36:40])
    compressed = bool(flags & 1)
    raw = sm[("BodyText", "Section0")]
    data = zlib.decompress(raw, -15) if compressed else raw
    return parse_records(data), (1 if compressed else 0), sm


# --------------------------------------------------------------------------- #
# 메인
# --------------------------------------------------------------------------- #
class TemplateError(RuntimeError):
    pass


def render_hwp(
    quote: Quote,
    out_path: str | Path,
    template: Optional[str | Path] = None,
    extra: Optional[dict] = None,     # 견적서/거래명세서는 사용 안 함
) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    template = Path(template) if template else _default_template()

    streams = cfbf.read_streams(str(template))
    records, compressed, sm = _read_section(streams)

    strip_memo_controls(records)
    _edit_top_fields(records, quote)
    _remove_template_notes(records)
    _rebuild_item_table(records, quote)

    new_body = serialize_records(records)
    _validate(new_body)

    # 재압축 후 스트림 교체
    if compressed:
        co = zlib.compressobj(9, zlib.DEFLATED, -15)
        packed = co.compress(new_body) + co.flush()
    else:
        packed = new_body

    new_streams = []
    for parts, data in streams:
        if tuple(parts) == ("BodyText", "Section0"):
            new_streams.append((parts, packed))
        else:
            new_streams.append((parts, data))

    cfbf.write_cfbf(str(out_path), new_streams)
    return out_path


# --------------------------------------------------------------------------- #
# 1) 상단 전역 필드: 발급일자 · 대학명
# --------------------------------------------------------------------------- #
def _edit_top_fields(records: List[Record], quote: Quote) -> None:
    d = quote.issue_date
    date_text = f"서기   {d.year}년    {d.month:02d}월    {d.day:02d}일"
    univ_text = f"{quote.university}  귀중"

    done_date = done_univ = False
    for i, r in enumerate(records):
        if r.tag != PARA_TEXT:
            continue
        t = text_of(r)
        hdr = _prev_para_header(records, i)
        if not done_date and "서기" in t and "년" in t and "월" in t:
            if hdr:
                set_plain_text(hdr, r, date_text)
                done_date = True
        elif not done_univ and "귀중" in t:
            if hdr:
                set_plain_text(hdr, r, univ_text)
                done_univ = True
    if not (done_date and done_univ):
        raise TemplateError("템플릿에서 발급일자/대학명 칸을 찾지 못했습니다.")


# 템플릿 하단 작성 안내 문구 (실제 견적서에는 나오면 안 됨)
_NOTE_MARKERS = (
    "총 금액", "한글로 작성", "Ex)", "1,650,000",
    "일백육십오만", "신청서에 맞게", "베이직 경우",
)


def _remove_template_notes(records: List[Record]) -> None:
    """템플릿에 남아있는 작성 안내 문구 문단을 빈 문단으로 만든다."""
    for i, r in enumerate(records):
        if r.tag != PARA_TEXT:
            continue
        if any(m in text_of(r) for m in _NOTE_MARKERS):
            hdr = _prev_para_header(records, i)
            if hdr:
                set_plain_text(hdr, r, "")


def _prev_para_header(records: List[Record], txt_idx: int) -> Optional[Record]:
    for j in range(txt_idx - 1, -1, -1):
        if records[j].tag == PARA_HEADER:
            return records[j]
        if records[j].tag == PARA_TEXT:
            return None
    return None


# --------------------------------------------------------------------------- #
# 2) 품목 테이블 재구성
# --------------------------------------------------------------------------- #
def _find_item_table(records: List[Record]) -> Tuple[int, int, int]:
    """품목 테이블(14행 8열)의 (TABLE 인덱스, 영역끝 인덱스, table_level)."""
    for i, r in enumerate(records):
        if r.tag == TABLE:
            ncols = struct.unpack("<H", r.payload[6:8])[0]
            if ncols == 8:
                level = r.level
                j = i + 1
                while j < len(records) and records[j].level >= level:
                    j += 1
                return i, j, level
    raise TemplateError("품목 테이블(8열)을 찾지 못했습니다.")


def _rebuild_item_table(records: List[Record], quote: Quote) -> None:
    ti, end, level = _find_item_table(records)
    table_rec = records[ti]
    cells = _split_cells(records[ti + 1:end], level)

    # 행 인덱스별 그룹화
    rows: Dict[int, List[Cell]] = {}
    for c in cells:
        rows.setdefault(c.row(), []).append(c)
    row_ids = sorted(rows)
    total_row_id = row_ids[-1]           # 마지막 = 합 계 행
    summary_row_id = row_ids[0]          # 첫 = 합계금액 행
    header_row_id = row_ids[1]           # 열 제목 행

    # 템플릿 데이터 행(2..) 을 의미별로 확인
    tpl = _index_template_rows(rows, row_ids)

    # 주문 -> 선택 행(Cell 목록의 목록)
    selected: List[List[Cell]] = []
    for sel_cells in _select_rows(quote, tpl):
        selected.append(sel_cells)

    # 합계금액(요약) 셀 편집
    _edit_amount_cell(rows[summary_row_id], quote)
    # 합 계 행의 금액 셀 편집
    _edit_total_cell(rows[total_row_id], quote)

    # 출력 행 순서: 요약, 헤더, 선택된 데이터 행..., 합계
    out_rows: List[List[Cell]] = [rows[summary_row_id], rows[header_row_id]]
    out_rows.extend(selected)
    out_rows.append(rows[total_row_id])

    # 재번호 + 셀 직렬화
    new_cell_records: List[Record] = []
    row_sizes: List[int] = []
    for new_idx, cell_group in enumerate(out_rows):
        row_sizes.append(len(cell_group))
        for c in cell_group:
            c.set_row(new_idx)
            new_cell_records.extend(c.records)

    # TABLE 레코드 갱신 (nRows + rowSize 배열, 꼬리 바이트 보존)
    p = table_rec.payload
    old_nrows = struct.unpack("<H", p[4:6])[0]
    tail = p[18 + 2 * old_nrows:]
    new_payload = bytearray()
    new_payload += p[0:4]
    new_payload += struct.pack("<H", len(out_rows))
    new_payload += p[6:18]
    for rs in row_sizes:
        new_payload += struct.pack("<H", rs)
    new_payload += tail
    table_rec.payload = bytes(new_payload)

    # 레코드 리스트에서 테이블 영역 교체
    records[ti + 1:end] = new_cell_records


def _index_template_rows(rows, row_ids) -> dict:
    """데이터 행을 의미(도구·등급·부가서비스)별로 매핑하고 검증한다."""
    data_ids = row_ids[2:-1]             # 요약·헤더·합계 제외
    tpl = {}
    for rid in data_ids:
        cells = rows[rid]
        by_col = {c.col(): c for c in cells}
        spec = by_col.get(2).first_text() if 2 in by_col else ""
        name0 = by_col.get(0).first_text() if 0 in by_col else ""
        name1 = by_col.get(1).first_text() if 1 in by_col else ""
        if spec == "PREMIER" and "K-NSSE" in name0:
            tpl[("K", "main")] = cells
        elif spec == "PREMIER" and "UICA" in name0:
            tpl[("U", "main")] = cells
        elif spec == "BASIC":
            tpl["basic"] = cells
        elif "단과대학별" in name1:
            tpl[("K", "addon", 1)] = cells
        elif "Peer Benchmarking" in name1:
            # K 본품 다음이면 K2, U 본품 다음이면 U1 — 행 순서로 구분
            if ("K", "main") in tpl and ("U", "main") not in tpl:
                tpl[("K", "addon", 2)] = cells
            else:
                tpl[("U", "addon", 1)] = cells

    required = [("K", "main"), ("K", "addon", 1), ("K", "addon", 2),
                ("U", "main"), ("U", "addon", 1), "basic"]
    missing = [k for k in required if k not in tpl]
    if missing:
        raise TemplateError(f"템플릿 데이터 행 매핑 실패: {missing}")
    return tpl


def _select_rows(quote: Quote, tpl: dict) -> List[List[Cell]]:
    """견적 품목을 템플릿 데이터 행(복제본)에 매핑한다."""
    from .catalog import CATALOG
    from .engine import parse_codes

    result: List[List[Cell]] = []
    basic_used = False
    # 견적 코드를 다시 파싱해 도구·등급·부가 구조를 얻는다
    for code in quote.source_codes:
        pass
    # source_codes 는 토큰 단위이므로 그대로 순회
    selections = [s for c in quote.source_codes for s in parse_codes(c)]

    for sel in selections:
        if sel.grade == "P":
            main = _dup(tpl[(sel.tool, "main")])
            result.append(main)
            for num in sel.addons:
                result.append(_dup(tpl[(sel.tool, "addon", num)]))
        else:  # BASIC
            basic = _dup(tpl["basic"])
            if sel.tool == "K":
                # 템플릿 basic 은 UICA -> K-NSSE 품명으로 교체
                by_col = {c.col(): c for c in basic}
                by_col[0].set_single_text(CATALOG["K"].product_name)
            basic_used = True
            result.append(basic)
    return result


def _dup(cells: List[Cell]) -> List[Cell]:
    return [c.clone() for c in cells]


def _edit_amount_cell(summary_cells: List[Cell], quote: Quote) -> None:
    """합계금액 요약 셀(누름틀 포함)을 순수 텍스트로 교체."""
    text = f"{quote.korean_amount}(\\  {quote.grand_total:,} )"
    # 가장 넓은(colspan 큰) 셀이 금액 칸
    target = max(summary_cells, key=lambda c: c.colspan())
    target.set_single_text(text)


def _edit_total_cell(total_cells: List[Cell], quote: Quote) -> None:
    """합 계 행의 금액 셀을 교체."""
    won = f"\\  {quote.grand_total:,} "
    # '합 계' 글자가 없는, 금액이 들어갈 셀을 고른다
    candidates = [c for c in total_cells if "합" not in c.first_text()]
    target = max(candidates, key=lambda c: c.colspan()) if candidates else total_cells[-1]
    target.set_single_text(won)


# --------------------------------------------------------------------------- #
# 3) 출력 무결성 검증
# --------------------------------------------------------------------------- #
def _validate(body: bytes) -> None:
    """재파싱해 표 불변식을 확인한다 (한글에서 열리기 위한 최소 조건)."""
    records = parse_records(body)
    if serialize_records(records) != body:
        raise TemplateError("검증 실패: 레코드 재직렬화 불일치")

    ti, end, level = _find_item_table(records)
    table_rec = records[ti]
    nrows = struct.unpack("<H", table_rec.payload[4:6])[0]
    ncols = struct.unpack("<H", table_rec.payload[6:8])[0]
    row_sizes = [
        struct.unpack("<H", table_rec.payload[18 + 2 * k:20 + 2 * k])[0]
        for k in range(nrows)
    ]

    cells = _split_cells(records[ti + 1:end], level)
    # 행별 셀 수 == rowSize
    grid: Dict[int, List[Tuple[int, int]]] = {}
    for c in cells:
        grid.setdefault(c.row(), []).append((c.col(), c.colspan()))

    if sorted(grid) != list(range(nrows)):
        raise TemplateError(f"검증 실패: 행 번호가 0..{nrows-1} 연속이 아님 ({sorted(grid)})")

    for r in range(nrows):
        cols = grid[r]
        if len(cols) != row_sizes[r]:
            raise TemplateError(
                f"검증 실패: 행 {r} 셀 수 {len(cols)} != rowSize {row_sizes[r]}"
            )
        covered = 0
        for col, span in cols:
            covered += span
        if covered != ncols:
            raise TemplateError(
                f"검증 실패: 행 {r} 열 합 {covered} != {ncols}"
            )
