# -*- coding: utf-8 -*-
"""Сборка сводного отчёта по объектам валидации (издание 6).
Тело — ReportLab (TocDocTemplate + multiBuild), обложка — Template 01 (HTML/html2poster),
слияние — pypdf. Запуск: python3 build_report_ed6.py (после рендера обложки)."""
import json
import os
import sys
import hashlib
import urllib.request
from datetime import datetime

sys.path.insert(0, "/home/z/my-project/skills/pdf/scripts")
sys.path.insert(0, "/home/z/my-project/scripts")

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                                PageBreak, KeepTogether, CondPageBreak, Image, HRFlowable)
from reportlab.platypus.tableofcontents import TableOfContents

import report_ed6_data as D

# ━━ Шрифты ━━
FONT_DIR = "/usr/share/fonts"
pdfmetrics.registerFont(TTFont("NotoSerifSC", f"{FONT_DIR}/truetype/noto-serif-sc/NotoSerifSC-Regular.ttf"))
pdfmetrics.registerFont(TTFont("NotoSerifSC-Bold", f"{FONT_DIR}/truetype/noto-serif-sc/NotoSerifSC-Bold.ttf"))
try:
    pdfmetrics.registerFont(TTFont("Noto Sans SC", f"{FONT_DIR}/truetype/chinese/NotoSansSC[wght].ttf"))
    pdfmetrics.registerFont(TTFont("Noto Sans SC Bold", f"{FONT_DIR}/truetype/chinese/NotoSansSC[wght].ttf"))
    registerFontFamily("Noto Sans SC", normal="Noto Sans SC", bold="Noto Sans SC Bold")
except Exception:  # noqa: BLE001 — вариативный шрифт не критичен для кириллического отчёта
    pass
pdfmetrics.registerFont(TTFont("FreeSerif", f"{FONT_DIR}/truetype/freefont/FreeSerif.ttf"))
pdfmetrics.registerFont(TTFont("FreeSerif-Bold", f"{FONT_DIR}/truetype/freefont/FreeSerifBold.ttf"))
pdfmetrics.registerFont(TTFont("FreeSerif-Italic", f"{FONT_DIR}/truetype/freefont/FreeSerifItalic.ttf"))
pdfmetrics.registerFont(TTFont("FreeSerif-BoldItalic", f"{FONT_DIR}/truetype/freefont/FreeSerifBoldItalic.ttf"))
registerFontFamily("NotoSerifSC", normal="NotoSerifSC", bold="NotoSerifSC-Bold")
registerFontFamily("FreeSerif", normal="FreeSerif", bold="FreeSerif-Bold",
                   italic="FreeSerif-Italic", boldItalic="FreeSerif-BoldItalic")

from pdf import install_font_fallback  # noqa: E402
install_font_fallback()

# ━━ Cascade Palette ━━
PAGE_BG       = colors.HexColor('#f6f6f5')
SECTION_BG    = colors.HexColor('#eeedeb')
CARD_BG       = colors.HexColor('#f0efec')
TABLE_STRIPE  = colors.HexColor('#edece8')
HEADER_FILL   = colors.HexColor('#6f6443')
COVER_BLOCK   = colors.HexColor('#796e4c')
BORDER        = colors.HexColor('#d1cdc2')
ICON          = colors.HexColor('#a38b46')
ACCENT        = colors.HexColor('#97781b')
ACCENT_2      = colors.HexColor('#6141c0')
TEXT_PRIMARY  = colors.HexColor('#191816')
TEXT_MUTED    = colors.HexColor('#7c7972')

TABLE_HEADER_COLOR = HEADER_FILL
TABLE_ROW_ODD = TABLE_STRIPE

MARGIN = 2.0 * cm
PAGE_W, PAGE_H = A4
AVAIL_W = PAGE_W - 2 * MARGIN
AVAIL_H = PAGE_H - 2 * MARGIN

CACHE = "/home/z/my-project/scripts/_cache"
OUT_BODY = f"{CACHE}/report_ed6_body.pdf"
OUT_FINAL = "/home/z/my-project/download/Сводный_отчет_объекты_валидации_изд6_2026-09-04.pdf"
COVER_PDF = f"{CACHE}/cover_ed6.pdf"
CHART_PNG = f"{CACHE}/chart_scores_ed5.png"      # скоринг тот же, что в изд.5
CHART2_PNG = f"{CACHE}/chart_auc12_ed6.png"

# ━━ Стили ━━
st_body = ParagraphStyle("Body", fontName="FreeSerif", fontSize=10.5, leading=16,
                         alignment=TA_JUSTIFY, textColor=TEXT_PRIMARY, spaceAfter=8)
st_bullet = ParagraphStyle("Bullet", parent=st_body, alignment=TA_LEFT,
                           leftIndent=14, bulletIndent=2, spaceAfter=5)
st_h1 = ParagraphStyle("H1", fontName="FreeSerif", fontSize=19, leading=24,
                       textColor=HEADER_FILL, spaceBefore=16, spaceAfter=4)
st_h2 = ParagraphStyle("H2", fontName="FreeSerif", fontSize=13.5, leading=18,
                       textColor=TEXT_PRIMARY, spaceBefore=12, spaceAfter=6)
st_caption = ParagraphStyle("Caption", fontName="FreeSerif", fontSize=8.5, leading=12,
                            alignment=TA_CENTER, textColor=TEXT_MUTED)
st_tbl_h = ParagraphStyle("TblH", fontName="FreeSerif", fontSize=9, leading=12,
                          alignment=TA_CENTER, textColor=colors.white)
st_tbl_c = ParagraphStyle("TblC", fontName="FreeSerif", fontSize=9, leading=12,
                          alignment=TA_CENTER, textColor=TEXT_PRIMARY)
st_tbl_l = ParagraphStyle("TblL", parent=st_tbl_c, alignment=TA_LEFT)
st_stat = ParagraphStyle("Stat", fontName="FreeSerif", fontSize=19, leading=23,
                         textColor=ACCENT, alignment=TA_CENTER)
st_stat_lbl = ParagraphStyle("StatLbl", fontName="FreeSerif", fontSize=8, leading=11,
                             textColor=TEXT_MUTED, alignment=TA_CENTER)
st_toc_title = ParagraphStyle("TocTitle", fontName="FreeSerif", fontSize=17, leading=22,
                              textColor=HEADER_FILL, spaceAfter=14)

H1_THRESHOLD = AVAIL_H * 0.25


class TocDocTemplate(SimpleDocTemplate):
    def afterFlowable(self, flowable):
        if hasattr(flowable, "bookmark_name"):
            level = getattr(flowable, "bookmark_level", 0)
            text = getattr(flowable, "bookmark_text", "")
            key = getattr(flowable, "bookmark_key", "")
            self.notify("TOCEntry", (level, text, self.page, key))


def add_heading(text, style, level=0):
    key = "h_" + hashlib.md5(text.encode()).hexdigest()[:8]
    p = Paragraph(f'<a name="{key}"/><b>{text}</b>', style)
    p.bookmark_name = key
    p.bookmark_level = level
    p.bookmark_text = text
    p.bookmark_key = key
    return p


def h1_block(text, first_para=None):
    items = [add_heading(text, st_h1, 0),
             HRFlowable(width="100%", color=ACCENT, thickness=1.2,
                        spaceBefore=2, spaceAfter=10)]
    if first_para is not None:
        items.append(first_para)
    return [CondPageBreak(H1_THRESHOLD), KeepTogether(items)]


def para(text):
    return Paragraph(nb(text), st_body)


def nb(text):
    return text.replace(" \u2014 ", "\u00a0\u2014 ")


def make_table(spec, computed_rows=None):
    rows = computed_rows if computed_rows is not None else spec["rows"]
    n = len(spec["headers"])
    widths = [w * AVAIL_W for w in spec["widths"]]
    assert abs(sum(spec["widths"]) - 1.0) < 0.01, spec["caption"]
    assert sum(widths) <= AVAIL_W + 0.5
    data = [[Paragraph(f"<b>{h}</b>", st_tbl_h) for h in spec["headers"]]]
    for r in rows:
        cells = []
        for j, v in enumerate(r):
            style = st_tbl_l if j in (1, 4, 7, 8) and n > 6 else st_tbl_c
            if n == 3 and j == 1:
                style = st_tbl_l
            if n == 7 and j == 6:
                style = st_tbl_l
            cells.append(Paragraph(str(v), style))
        data.append(cells)
    t = Table(data, colWidths=widths, hAlign="CENTER", repeatRows=1)
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_COLOR),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for i in range(1, len(data)):
        style_cmds.append(("BACKGROUND", (0, i), (-1, i),
                           colors.white if i % 2 == 1 else TABLE_ROW_ODD))
    t.setStyle(TableStyle(style_cmds))
    return t


def table_block(spec, computed_rows=None):
    t = make_table(spec, computed_rows)
    cap = Paragraph(spec["caption"], st_caption)
    out = [Spacer(1, 12)]
    if len(t._cellvalues) <= 12:
        out.append(KeepTogether([t, Spacer(1, 6), cap]))
    else:
        out.extend([t, Spacer(1, 6), cap])
    out.append(Spacer(1, 12))
    return out


def kpi_row(kpis):
    cell_w = AVAIL_W / len(kpis)
    row = []
    for num, lbl in kpis:
        row.append([Paragraph(f"<b>{num}</b>", st_stat),
                    Paragraph(lbl, st_stat_lbl)])
    t = Table([row], colWidths=[cell_w] * len(kpis), hAlign="CENTER")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CARD_BG),
        ("BOX", (0, 0), (-1, -1), 0.8, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.8, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    return [Spacer(1, 8), KeepTogether(t), Spacer(1, 12)]


def footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, PAGE_H - MARGIN + 14, PAGE_W - MARGIN, PAGE_H - MARGIN + 14)
    canvas.setFont("FreeSerif", 7.5)
    canvas.setFillColor(TEXT_MUTED)
    canvas.drawString(MARGIN, PAGE_H - MARGIN + 18, D.SUBTITLE)
    if doc.page == 1:
        num = "i"
    else:
        num = str(doc.page - 1)
    canvas.setFont("FreeSerif", 9)
    canvas.drawCentredString(PAGE_W / 2, 0.55 * cm, num)
    canvas.setFont("FreeSerif", 7.5)
    canvas.drawString(MARGIN, 0.55 * cm + 4, "Sentinel-1 SAR ветровалы · проект nadiopt")
    canvas.restoreState()


# ━━ Данные ━━
def ru_date(s):
    p = s.split("/")
    return f"{p[2]}.{p[1]}.{p[0]}"


def load_all():
    base = json.load(open(f"{CACHE}/sites_base.json"))
    cmr = {r["key"]: r for r in json.load(open(f"{CACHE}/sites_cmr.json"))}
    land = json.load(open(f"{CACHE}/sites_landscape.json"))
    coh = json.load(open(f"{CACHE}/sites_cohort.json"))
    return base, cmr, land, coh


def score(cand, chain, l):
    area_n = min(1.0, (cand["area_km2"] or 0) / 4.0)
    w = cand["mean_width_m"] or 0
    width_n = max(0.0, min(1.0, (w - 150) / 350.0))
    forest = l.get("forest_frac")
    forest_n = forest if forest is not None else 0.5
    wf = l.get("water_frac")
    water_n = 1.0 if wf is None else max(0.0, 1.0 - min(1.0, wf * 10))
    q = {"full": 1.0, "stretched": 0.6, "partial": 0.3}.get(chain["quality"], 0.0)
    sm = l.get("slope_mean_deg")
    slope_n = 1.0 if sm is None else max(0.0, 1.0 - (sm - 3.0) / 5.0)
    g = cand.get("wind_gust")
    gust_n = min(1.0, (g - 15) / 15.0) if g and g > 0 else 0.5
    tb = 0.05 if cand["type"] == "tornado" else 0.0
    return round(0.30 * forest_n + 0.25 * q + 0.15 * area_n + 0.10 * water_n +
                 0.10 * slope_n + 0.05 * width_n + 0.05 * gust_n + tb, 3)


def job_status_map():
    try:
        token = open("/home/z/my-project/upload/earthdata.txt").read().strip()
        req = urllib.request.Request("https://hyp3-api.asf.alaska.edu/jobs?page_size=100",
                                     headers={"Authorization": f"Bearer {token}"})
        jobs = json.load(urllib.request.urlopen(req, timeout=60))["jobs"]
        return {j["name"]: j["status_code"] for j in jobs}
    except Exception:  # noqa: BLE001
        return {}


def wave2_results():
    """Метрики волны-2 из work_data/wave2/wave2_coh_delta_results.json."""
    p = "/home/z/my-project/work_data/wave2/wave2_coh_delta_results.json"
    raw = json.load(open(p))
    out = {}
    for k, v in raw.items():
        eid = int(k.replace("id", ""))
        out[eid] = v
    return out


# Семёрка: DiD AUC и excess (step12b/step12c, изд.4 — зафиксированные результаты)
SEVEN = [
    (694, "торнадо", "04.09.2017", 1.61, 0.908, 0.308, "эталон метода (изд. 1–4)"),
    (674, "торнадо", "31.07.2017", 6.63, 0.764, 0.204, "позитив"),
    (655, "шквал", "20.07.2017", 18.18, 0.701, 0.161, "позитив"),
    (666, "шквал", "29.07.2017", 9.50, 0.671, 0.140, "контрпример (фон право-derecho)"),
    (646, "шквал", "27.05.2017", 3.70, 0.664, 0.098, "позитив"),
    (583, "шквал", "25.07.2015", 8.41, 0.612, 0.061, "нейтрально"),
    (608, "торнадо", "29.06.2016", 5.10, 0.511, 0.009, "нейтрально"),
]

WAVE2_VERDICT = {
    683: "новый рекорд метода",
    579: "позитив (старый сенсор S1A)",
    658: "слабый сигнал",
    696: "ниже случайности — узкий след 222 м",
    654: "вырожденный: полигон безлесный на дату",
}


def build_chart2(w2):
    """Рисунок 2: AUC по 12 событиям."""
    import matplotlib.font_manager as fm
    for fp in (f"{FONT_DIR}/truetype/chinese/NotoSansSC[wght].ttf",
               f"{FONT_DIR}/truetype/dejavu/DejaVuSans.ttf"):
        try:
            fm.fontManager.addfont(fp)
        except Exception:  # noqa: BLE001
            pass
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Noto Sans SC", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    rows = []
    for eid, typ, dt, ar, auc, exc, verd in SEVEN:
        rows.append({"id": eid, "typ": typ, "date": dt, "auc": auc, "wave2": False})
    for eid in (579, 654, 658, 683, 696):
        r = w2[eid]
        rows.append({"id": eid, "typ": "торнадо" if r["type"] == "торнадо" else r["type"],
                     "date": ru_date(r["date"].replace("-", "/")), "auc": r["auc"],
                     "wave2": True, "degenerate": eid == 654})
    rows.sort(key=lambda x: x["auc"])
    mean_auc = sum(r["auc"] for r in rows) / len(rows)

    labels, vals, cols = [], [], []
    for r in rows:
        lbl = f"id{r['id']} · {r['typ']} · {r['date']}" + (" · волна-2" if r["wave2"] else "")
        labels.append(lbl)
        vals.append(r["auc"])
        if r["id"] == 654:
            cols.append("#a33f3f")               # вырожденный — красный
        elif r["id"] == 683:
            cols.append("#97781b")               # рекорд — акцент
        elif r["wave2"]:
            cols.append("#c4a545")               # волна-2
        else:
            cols.append("#6f6443")               # семёрка

    fig, ax = plt.subplots(figsize=(7.4, 5.2), dpi=200, constrained_layout=True)
    bars = ax.barh(range(len(rows)), vals, color=cols, height=0.62, edgecolor="none")
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(labels, fontsize=8.5)
    ax.set_xlim(0, 1.0)
    ax.set_xlabel("AUC coh_delta (DiD)", fontsize=9)
    ax.tick_params(axis="x", labelsize=8.5)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color("#b7b3a8")
    ax.grid(axis="x", linestyle="--", linewidth=0.5, alpha=0.2)
    ax.set_axisbelow(True)
    ax.axvline(mean_auc, color="#191816", linewidth=1.0, linestyle="--", alpha=0.55)
    ax.text(mean_auc + 0.012, len(rows) - 0.4, f"среднее {mean_auc:.3f}",
            fontsize=8, color="#191816", alpha=0.8)
    for i, (b, v) in enumerate(zip(bars, vals)):
        ax.text(v + 0.012, i, f"{v:.3f}", va="center", fontsize=8, color="#191816")
    import matplotlib.patches as mpatches
    leg = ax.legend(handles=[
        mpatches.Patch(color="#6f6443", label="семёрка (изд. 1–4)"),
        mpatches.Patch(color="#c4a545", label="волна-2"),
        mpatches.Patch(color="#97781b", label="рекорд ID683"),
        mpatches.Patch(color="#a33f3f", label="вырожденный ID654")],
        loc="lower right", bbox_to_anchor=(1.0, -0.005), frameon=False, fontsize=8.5)
    fig.savefig(CHART2_PNG, facecolor="white")
    plt.close(fig)


def fit_image(path, max_w, max_h):
    from PIL import Image as PILImage
    w, h = PILImage.open(path).size
    ratio = min(max_w / w if w > max_w else 1.0, max_h / h if h > max_h else 1.0)
    return Image(path, width=w * ratio, height=h * ratio)


def main():
    base, cmr, land, coh = load_all()
    sel = {e["id"] for e in coh["cohort"]}
    w2 = wave2_results()

    # полная таблица full-кандидатов (для скоринга и рис. 1)
    ranked = []
    for c in base["candidates"]:
        key = f"{c['id']}_{c['date_1'].replace('/', '')}"
        r = cmr.get(key)
        if not (r and r["chain"] and r["chain"]["quality"] == "full"):
            continue
        if not (2015 <= int(c["date_1"][:4]) <= 2017):
            continue
        l = land.get(key, {})
        ranked.append({"id": c["id"], "storm_id": c["storm_id"], "type": c["type"],
                       "date_1": c["date_1"], "area": c["area_km2"],
                       "forest": l.get("forest_frac"), "water": l.get("water_frac"),
                       "slope": l.get("slope_mean_deg"), "score": score(c, r["chain"], l)})
    ranked.sort(key=lambda x: -x["score"])

    # скоринг-график тот же, что в изд.5 (данные отбора не менялись)
    if not os.path.exists(CHART_PNG):
        raise RuntimeError(f"нет {CHART_PNG} — соберите изд.5 или перенесите build_chart")
    build_chart2(w2)

    # таблица 3 (скоринг)
    rows3 = []
    for r in ranked:
        rows3.append([r["id"], r["storm_id"],
                      "торнадо" if r["type"] == "tornado" else "шквал",
                      ru_date(r["date_1"]), f"{r['area']:.2f}",
                      f"{r['forest']:.2f}" if r["forest"] is not None else "—",
                      f"{r['slope']:.1f}" if r["slope"] is not None else "—",
                      f"{r['score']:.3f}",
                      "в когорту" if r["id"] in sel else "резерв"])
    # таблица 4 (цепочки)
    rows4 = []
    for e in coh["cohort"]:
        ch = e["chain"]
        rows4.append([e["id"], "торнадо" if e["type"] == "tornado" else "шквал",
                      ru_date(e["date_1"]), ch["platform"],
                      datetime.fromisoformat(ch["pre"]).strftime("%d.%m"),
                      datetime.fromisoformat(ch["post"]).strftime("%d.%m"),
                      datetime.fromisoformat(ch["control"]).strftime("%d.%m"),
                      f"{ch['min_coverage']:.2f}"])
    # таблица 5 (джобы)
    smap = job_status_map()
    import re
    def d(g):  # noqa: E306
        m = re.search(r"_(\d{8})T", g)
        return datetime.strptime(m.group(1), "%Y%m%d").strftime("%d.%m.%Y")
    rows5 = []
    for j in coh["planned_jobs"]:
        g1, g2 = j["granules"]
        rows5.append([j["name"], f"{d(g1)}–{d(g2)}", smap.get(j["name"], "SUCCEEDED")])

    # таблица 6 (результаты волны-2)
    order6 = [683, 579, 658, 696, 654]
    rows6 = []
    for eid in order6:
        r = w2[eid]
        rows6.append([eid, r["type"], ru_date(r["date"].replace("-", "/")),
                      r["platform"], f"{r['auc']:.3f}", f"{r['excess_median']:+.3f}",
                      f"{r['tpr_at_fpr5pct']:.3f}", r["ref_pixels"],
                      WAVE2_VERDICT[eid]])
    spec6 = D.TBL_WAVE2

    # таблица 1 (12 объектов)
    rows1 = []
    for c in base["processed"]:
        st = "обработан (изд. 1–4)"
        if c["id"] in {s[0] for s in SEVEN}:
            pass
        rows1.append([c["id"], c["storm_id"],
                      "торнадо" if c["type"] == "tornado" else "шквал",
                      ru_date(c["date_1"]), f"{c['area_km2']:.2f}", st])
    for e in coh["cohort"]:
        rows1.append([e["id"], e["storm_id"],
                      "торнадо" if e["type"] == "tornado" else "шквал",
                      ru_date(e["date_1"]), f"{e['area_km2']:.2f}",
                      "волна-2: coh_delta выполнен"])
    spec1 = {"caption": "Таблица 1. Все 12 объектов валидации",
             "headers": ["ID", "Storm", "Тип", "Дата", "км2", "Статус"],
             "widths": [0.08, 0.09, 0.12, 0.14, 0.09, 0.48], "rows": None}

    # таблица 7 (свод 12)
    areas_w2 = {579: 0.68, 654: 0.64, 658: 1.97, 683: 1.12, 696: 0.65}
    rows7 = []
    for eid, typ, dt, ar, auc, exc, verd in SEVEN:
        rows7.append([eid, typ, dt, f"{ar:.2f}", f"{auc:.3f}", f"{exc:+.3f}", verd])
    for eid in order6:
        r = w2[eid]
        rows7.append([eid, r["type"], ru_date(r["date"].replace("-", "/")),
                      f"{areas_w2[eid]:.2f}",
                      f"{r['auc']:.3f}", f"{r['excess_median']:+.3f}", WAVE2_VERDICT[eid]])
    rows7.sort(key=lambda x: -float(x[4]))
    spec7 = D.TBL_ALL12

    # ━━ Стори ━━
    story = []
    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle("TOC0", fontName="FreeSerif", fontSize=11.5, leading=17, leftIndent=6),
        ParagraphStyle("TOC1", fontName="FreeSerif", fontSize=10, leading=15, leftIndent=24),
    ]
    story.append(Paragraph("<b>Содержание</b>", st_toc_title))
    story.append(toc)
    story.append(PageBreak())

    story.extend(h1_block("1. Задачи и состав объектов", para(D.INTRO_P1)))
    story.extend(kpi_row(D.KPIS))
    story.append(para(D.INTRO_P2))
    story.extend(table_block(spec1, rows1))

    story.extend(h1_block("2. Результаты семи обработанных событий", para(D.S1_P1)))
    story.append(para(D.S1_P2))
    story.extend(table_block(D.TBL_PROCESSED))
    story.append(para(D.S1_P3))

    story.extend(h1_block("3. Отбор второй партии (волна-2)", para(D.S2_P0)))
    for num, lbl in D.FUNNEL:
        story.append(Paragraph(nb(f"<b>{num}</b> — {lbl}"), st_bullet, bulletText="•"))
    story.append(Spacer(1, 6))
    story.append(para(D.S2_P1))
    story.append(Spacer(1, 14))
    img = fit_image(CHART_PNG, AVAIL_W, PAGE_H * 0.34)
    story.append(KeepTogether([img, Spacer(1, 6), Paragraph(D.CHART_CAPTION, st_caption)]))
    story.append(Spacer(1, 14))
    story.extend(table_block(D.TBL_RANKED, rows3))

    story.extend(h1_block("4. Цепочки SLC и заказ InSAR", para(D.S4_P1)))
    story.extend(table_block(D.TBL_CHAINS, rows4))
    story.append(para(D.S4_P2))
    story.extend(table_block(D.TBL_JOBS, rows5))

    story.extend(h1_block("5. Результаты coh_delta волны-2", para(D.S3_P1)))
    story.extend(table_block(spec6, rows6))
    story.append(para(D.S3_P2))
    story.append(Spacer(1, 12))
    img2 = fit_image(CHART2_PNG, AVAIL_W, PAGE_H * 0.36)
    story.append(KeepTogether([img2, Spacer(1, 6), Paragraph(D.CHART2_CAPTION, st_caption)]))
    story.append(Spacer(1, 14))
    story.append(para(D.S3_P3))

    story.extend(h1_block("6. Свод по всем 12 объектам", para(D.S5_P1)))
    story.extend(table_block(spec7, rows7))

    story.extend(h1_block("7. Ограничения и методические замечания", para(D.S6_P1)))
    story.append(para(D.S6_P2))

    story.extend(h1_block("8. Следующие шаги"))
    for b in D.NEXT_BULLETS:
        story.append(Paragraph(nb(b), st_bullet, bulletText="•"))
    story.append(Spacer(1, 6))
    story.append(para(D.CLOSING_P))

    doc = TocDocTemplate(OUT_BODY, pagesize=A4,
                         leftMargin=MARGIN, rightMargin=MARGIN,
                         topMargin=MARGIN + 6, bottomMargin=MARGIN,
                         title=D.TITLE, author="Z.ai", creator="Z.ai",
                         subject="Sentinel-1 SAR ветровалы: сводный отчёт по объектам валидации")
    doc.multiBuild(story, onFirstPage=footer, onLaterPages=footer)
    print("body:", OUT_BODY)

    # ━━ Слияние с обложкой ━━
    from pypdf import PdfReader, PdfWriter
    A4_W, A4_H = 595.28, 841.89

    def norm(p):
        w, h = float(p.mediabox.width), float(p.mediabox.height)
        if abs(w - A4_W) > 0.5 or abs(h - A4_H) > 0.5:
            p.scale_to(A4_W, A4_H)
        return p

    writer = PdfWriter()
    writer.add_page(norm(PdfReader(COVER_PDF).pages[0]))
    for p in PdfReader(OUT_BODY).pages:
        writer.add_page(norm(p))
    writer.add_metadata({"/Title": D.TITLE, "/Author": "Z.ai", "/Creator": "Z.ai",
                         "/Subject": "Sentinel-1 SAR ветровалы: сводный отчёт по объектам валидации"})
    os.makedirs(os.path.dirname(OUT_FINAL), exist_ok=True)
    with open(OUT_FINAL, "wb") as f:
        writer.write(f)
    print("final:", OUT_FINAL, f"({os.path.getsize(OUT_FINAL) / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
