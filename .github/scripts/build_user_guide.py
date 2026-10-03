"""Build the illustrated BioTrack Studio PDF user guide from Markdown."""

from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Frame,
    Image,
    KeepTogether,
    LongTable,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents


NAVY = colors.HexColor("#18324A")
TEAL = colors.HexColor("#197C78")
INK = colors.HexColor("#1F2933")
MUTED = colors.HexColor("#52616B")
LIGHT = colors.HexColor("#EDF4F3")
PALE_BLUE = colors.HexColor("#EEF4F8")
LINE = colors.HexColor("#CBD5DC")
WHITE = colors.white


def register_fonts() -> None:
    candidates = {
        "Guide": Path("C:/Windows/Fonts/arial.ttf"),
        "Guide-Bold": Path("C:/Windows/Fonts/arialbd.ttf"),
        "Guide-Italic": Path("C:/Windows/Fonts/ariali.ttf"),
    }
    for name, path in candidates.items():
        if path.exists():
            pdfmetrics.registerFont(TTFont(name, str(path)))


def font(name: str) -> str:
    return name if name in pdfmetrics.getRegisteredFontNames() else {
        "Guide": "Helvetica",
        "Guide-Bold": "Helvetica-Bold",
        "Guide-Italic": "Helvetica-Oblique",
    }[name]


def make_styles():
    base = getSampleStyleSheet()
    styles = {
        "body": ParagraphStyle(
            "GuideBody",
            parent=base["BodyText"],
            fontName=font("Guide"),
            fontSize=10,
            leading=14,
            textColor=INK,
            spaceAfter=6,
        ),
        "small": ParagraphStyle(
            "GuideSmall",
            parent=base["BodyText"],
            fontName=font("Guide"),
            fontSize=8.5,
            leading=11,
            textColor=MUTED,
        ),
        "h1": ParagraphStyle(
            "GuideH1",
            parent=base["Heading1"],
            fontName=font("Guide-Bold"),
            fontSize=24,
            leading=29,
            textColor=NAVY,
            spaceBefore=2,
            spaceAfter=12,
            keepWithNext=True,
        ),
        "h2": ParagraphStyle(
            "GuideH2",
            parent=base["Heading2"],
            fontName=font("Guide-Bold"),
            fontSize=18,
            leading=22,
            textColor=NAVY,
            spaceBefore=0,
            spaceAfter=10,
            keepWithNext=False,
        ),
        "h3": ParagraphStyle(
            "GuideH3",
            parent=base["Heading3"],
            fontName=font("Guide-Bold"),
            fontSize=12.5,
            leading=16,
            textColor=TEAL,
            spaceBefore=10,
            spaceAfter=5,
            keepWithNext=True,
        ),
        "bullet": ParagraphStyle(
            "GuideBullet",
            parent=base["BodyText"],
            fontName=font("Guide"),
            fontSize=10,
            leading=14,
            leftIndent=14,
            firstLineIndent=-9,
            textColor=INK,
            spaceAfter=4,
        ),
        "code": ParagraphStyle(
            "GuideCode",
            parent=base["Code"],
            fontName="Courier",
            fontSize=8.5,
            leading=11,
            leftIndent=8,
            rightIndent=8,
            borderColor=LINE,
            borderWidth=0.5,
            borderPadding=7,
            backColor=colors.HexColor("#F6F8FA"),
            textColor=INK,
            spaceBefore=4,
            spaceAfter=8,
        ),
        "table": ParagraphStyle(
            "GuideTable",
            parent=base["BodyText"],
            fontName=font("Guide"),
            fontSize=8,
            leading=10,
            textColor=INK,
        ),
        "table_header": ParagraphStyle(
            "GuideTableHeader",
            parent=base["BodyText"],
            fontName=font("Guide-Bold"),
            fontSize=8,
            leading=10,
            textColor=WHITE,
        ),
        "cover_title": ParagraphStyle(
            "CoverTitle",
            parent=base["Title"],
            fontName=font("Guide-Bold"),
            fontSize=30,
            leading=36,
            alignment=TA_LEFT,
            textColor=NAVY,
            spaceAfter=8,
        ),
        "cover_subtitle": ParagraphStyle(
            "CoverSubtitle",
            parent=base["BodyText"],
            fontName=font("Guide"),
            fontSize=15,
            leading=20,
            textColor=TEAL,
            spaceAfter=20,
        ),
        "toc_title": ParagraphStyle(
            "TocTitle",
            parent=base["Heading1"],
            fontName=font("Guide-Bold"),
            fontSize=22,
            textColor=NAVY,
            spaceAfter=12,
        ),
    }
    return styles


def inline_markup(text: str) -> str:
    text = html.escape(text.strip())
    text = re.sub(r"`([^`]+)`", r'<font name="Courier">\1</font>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*([^*]+)\*", r"<i>\1</i>", text)
    text = re.sub(r"\[([^]]+)\]\(([^)]+)\)", r'<link href="\2" color="#197C78">\1</link>', text)
    return text


def make_callout(text: str, styles, color=TEAL):
    content = Paragraph(inline_markup(text), styles["body"])
    table = Table([[content]], colWidths=[166 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
                ("BOX", (0, 0), (-1, -1), 0.8, color),
                ("LINEBEFORE", (0, 0), (0, -1), 4, color),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


class GuideDocument(BaseDocTemplate):
    def __init__(self, filename: str, **kwargs):
        super().__init__(filename, **kwargs)
        frame = Frame(
            self.leftMargin,
            self.bottomMargin,
            self.width,
            self.height,
            id="content",
        )
        self.addPageTemplates(PageTemplate(id="guide", frames=[frame], onPage=self.draw_page))

    def draw_page(self, canvas, doc):
        canvas.saveState()
        width, height = A4
        canvas.setStrokeColor(TEAL)
        canvas.setLineWidth(1.2)
        canvas.line(20 * mm, height - 13 * mm, width - 20 * mm, height - 13 * mm)
        if doc.page > 1:
            canvas.setFont(font("Guide"), 8)
            canvas.setFillColor(MUTED)
            canvas.drawString(20 * mm, 12 * mm, "BioTrack Studio User Guide")
            canvas.drawRightString(width - 20 * mm, 12 * mm, f"Page {doc.page}")
        canvas.restoreState()

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and flowable.style.name in {"GuideH2", "GuideH3"}:
            level = 0 if flowable.style.name == "GuideH2" else 1
            text = flowable.getPlainText()
            key = f"heading-{abs(hash((text, self.page, level)))}"
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, level=level, closed=False)
            if level == 0:
                self.notify("TOCEntry", (level, text, self.page, key))


def cover_story(styles):
    start_table = Table(
        [
            [Paragraph("Windows", styles["table_header"]), Paragraph("macOS", styles["table_header"]), Paragraph("Linux", styles["table_header"])],
            [
                Paragraph("First use: setup_conda.bat<br/>Then: winstart.bat", styles["table"]),
                Paragraph("First use: bash setup_conda.sh<br/>Then: bash start.sh", styles["table"]),
                Paragraph("First use: bash setup_conda.sh<br/>Then: bash start.sh", styles["table"]),
            ],
        ],
        colWidths=[55 * mm, 55 * mm, 55 * mm],
    )
    start_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("BACKGROUND", (0, 1), (-1, -1), PALE_BLUE),
                ("BOX", (0, 0), (-1, -1), 0.7, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return [
        Spacer(1, 28 * mm),
        Paragraph("BioTrack Studio", styles["cover_title"]),
        Paragraph("Illustrated User Guide", styles["cover_subtitle"]),
        Paragraph(
            "A no-code workflow for cell segmentation, tracking, consensus-assisted review, and manual correction.",
            styles["body"],
        ),
        Spacer(1, 12 * mm),
        make_callout(
            "START HERE: Extract the complete ZIP, read this guide, and test the included demo_data before using experimental images.",
            styles,
        ),
        Spacer(1, 12 * mm),
        start_table,
        Spacer(1, 14 * mm),
        Paragraph(
            "Consensus markers show where algorithms disagree. They do not prove that a result is wrong. The reviewer makes the final decision from the microscopy image and biological context.",
            styles["body"],
        ),
        Spacer(1, 22 * mm),
        Paragraph("Research software. Not intended for clinical diagnosis.", styles["small"]),
        PageBreak(),
    ]


def toc_story(styles):
    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle(
            "TOCLevel1",
            fontName=font("Guide"),
            fontSize=10,
            leading=15,
            leftIndent=0,
            firstLineIndent=0,
            textColor=INK,
            spaceBefore=3,
        ),
        ParagraphStyle(
            "TOCLevel2",
            fontName=font("Guide"),
            fontSize=9,
            leading=13,
            leftIndent=12,
            firstLineIndent=0,
            textColor=MUTED,
        ),
    ]
    return [Paragraph("Contents", styles["toc_title"]), toc, PageBreak()]


def parse_table(lines, styles):
    rows = []
    for index, line in enumerate(lines):
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if index == 1 and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        style = styles["table_header"] if not rows else styles["table"]
        rows.append([Paragraph(inline_markup(cell), style) for cell in cells])
    count = max(len(row) for row in rows)
    if count == 2:
        widths = [52 * mm, 113 * mm]
    elif count == 3:
        widths = [40 * mm, 62.5 * mm, 62.5 * mm]
    else:
        widths = [165 * mm / count] * count
    table = LongTable(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE_BLUE]),
                ("GRID", (0, 0), (-1, -1), 0.4, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def parse_markdown(path: Path, styles):
    lines = path.read_text(encoding="utf-8").splitlines()
    story = []
    index = 0
    seen_h2 = False

    while index < len(lines):
        raw = lines[index]
        line = raw.strip()
        if not line:
            index += 1
            continue
        if line.startswith("# "):
            index += 1
            continue
        if line.startswith("## Contents"):
            index += 1
            while index < len(lines) and not lines[index].startswith("## "):
                index += 1
            continue
        if line.startswith("## "):
            heading_text = line[3:]
            if seen_h2:
                story.append(CondPageBreak(85 * mm))
            seen_h2 = True
            story.append(Paragraph(inline_markup(heading_text), styles["h2"]))
            index += 1
            continue
        if line.startswith("### "):
            story.append(Paragraph(inline_markup(line[4:]), styles["h3"]))
            index += 1
            continue
        image_match = re.fullmatch(r"!\[([^]]*)\]\(([^)]+)\)", line)
        if image_match:
            image_path = (path.parent / image_match.group(2)).resolve()
            with PILImage.open(image_path) as source:
                width_px, height_px = source.size
            max_width, max_height = 165 * mm, 175 * mm
            scale = min(max_width / width_px, max_height / height_px)
            picture = Image(str(image_path), width=width_px * scale, height=height_px * scale)
            caption = Paragraph(inline_markup(image_match.group(1)), styles["small"])
            story.append(KeepTogether([picture, Spacer(1, 2 * mm), caption, Spacer(1, 4 * mm)]))
            index += 1
            continue
        if line.startswith("```"):
            language = line[3:].strip()
            index += 1
            code = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code.append(lines[index])
                index += 1
            index += 1
            code_text = "<br/>".join(html.escape(item).replace(" ", "&nbsp;") for item in code)
            story.append(Paragraph(code_text or " ", styles["code"]))
            continue
        if line.startswith("|"):
            table_lines = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index].strip())
                index += 1
            story.extend([parse_table(table_lines, styles), Spacer(1, 4 * mm)])
            continue
        list_match = re.match(r"^(?:([-*])|(\d+)\.)\s+(.*)$", line)
        if list_match:
            marker = "•" if list_match.group(1) else f"{list_match.group(2)}."
            text = list_match.group(3)
            continuation = []
            index += 1
            while index < len(lines):
                candidate = lines[index]
                if not candidate.strip() or re.match(r"^(?:[-*]|\d+\.)\s+", candidate.strip()):
                    break
                if candidate.startswith("  "):
                    continuation.append(candidate.strip())
                    index += 1
                else:
                    break
            if continuation:
                text += " " + " ".join(continuation)
            story.append(Paragraph(f"{marker} {inline_markup(text)}", styles["bullet"]))
            continue

        paragraph_lines = [line]
        index += 1
        while index < len(lines):
            candidate = lines[index].strip()
            if (
                not candidate
                or candidate.startswith("#")
                or candidate.startswith("|")
                or candidate.startswith("```")
                or candidate.startswith("![")
                or re.match(r"^(?:[-*]|\d+\.)\s+", candidate)
            ):
                break
            paragraph_lines.append(candidate)
            index += 1
        paragraph = " ".join(paragraph_lines)
        if paragraph.startswith("**Important:**"):
            story.extend([make_callout(paragraph, styles), Spacer(1, 4 * mm)])
        else:
            story.append(Paragraph(inline_markup(paragraph), styles["body"]))

    return story


def build(protocol: Path, output: Path) -> None:
    register_fonts()
    styles = make_styles()
    output.parent.mkdir(parents=True, exist_ok=True)
    document = GuideDocument(
        str(output),
        pagesize=A4,
        rightMargin=22 * mm,
        leftMargin=22 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title="BioTrack Studio Illustrated User Guide",
        author="BioTrack Studio project team",
        subject="No-code cell segmentation, tracking, consensus review, and correction",
    )
    story = cover_story(styles) + toc_story(styles) + parse_markdown(protocol, styles)
    document.multiBuild(story)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.protocol.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
