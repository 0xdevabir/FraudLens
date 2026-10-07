#!/usr/bin/env python3
"""Build a printable FraudLens survey form with columns for 3 respondents."""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

OUT = Path(__file__).resolve().parents[1] / "printables" / "customer_survey_3_respondents.pdf"
FONT_CANDIDATES = [
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    Path("/Library/Fonts/Arial Unicode.ttf"),
]

PAGE_W, PAGE_H = A4
SIDE = 14 * mm
CONTENT_W = PAGE_W - 2 * SIDE
PERSON_W = CONTENT_W / 3

BRAND = colors.HexColor("#0B3D5C")
HEADER_BG = colors.HexColor("#E8EEF5")
GRID = colors.HexColor("#8A8A8A")
MUTED = colors.HexColor("#555555")


def register_font() -> str:
    for path in FONT_CANDIDATES:
        if path.is_file():
            pdfmetrics.registerFont(TTFont("SurveyFont", str(path)))
            return "SurveyFont"
    return "Helvetica"


def styles(font: str):
    base = getSampleStyleSheet()
    return {
        "brand": ParagraphStyle(
            "brand",
            parent=base["Heading1"],
            fontName=font,
            fontSize=16,
            leading=19,
            textColor=BRAND,
            alignment=TA_LEFT,
            spaceAfter=0,
        ),
        "tagline": ParagraphStyle(
            "tagline",
            parent=base["Normal"],
            fontName=font,
            fontSize=8,
            leading=10,
            textColor=MUTED,
            spaceAfter=1 * mm,
        ),
        "title": ParagraphStyle(
            "title",
            parent=base["Heading2"],
            fontName=font,
            fontSize=12,
            leading=15,
            alignment=TA_LEFT,
            textColor=colors.HexColor("#1a1a1a"),
            spaceBefore=2 * mm,
            spaceAfter=2 * mm,
        ),
        "h": ParagraphStyle(
            "h",
            parent=base["Heading2"],
            fontName=font,
            fontSize=10,
            leading=13,
            spaceBefore=3 * mm,
            spaceAfter=1.5 * mm,
            textColor=BRAND,
        ),
        "body": ParagraphStyle(
            "body",
            parent=base["Normal"],
            fontName=font,
            fontSize=8,
            leading=10.5,
            alignment=TA_LEFT,
            spaceAfter=1.5 * mm,
        ),
        "q": ParagraphStyle(
            "q",
            parent=base["Normal"],
            fontName=font,
            fontSize=8,
            leading=10.5,
            alignment=TA_LEFT,
        ),
        "opt": ParagraphStyle(
            "opt",
            parent=base["Normal"],
            fontName=font,
            fontSize=7,
            leading=9,
            textColor=colors.HexColor("#333333"),
        ),
        "note": ParagraphStyle(
            "note",
            parent=base["Normal"],
            fontName=font,
            fontSize=7,
            leading=9,
            textColor=MUTED,
            spaceAfter=1.5 * mm,
        ),
        "cell": ParagraphStyle(
            "cell",
            parent=base["Normal"],
            fontName=font,
            fontSize=7,
            leading=9,
            alignment=TA_LEFT,
        ),
    }


def answer_cell(opts: str, s) -> Paragraph:
    return Paragraph(opts.replace("\n", "<br/>"), s["cell"])


def three_col_table(header_cells, data_row, font: str):
    t = Table([header_cells, data_row], colWidths=[PERSON_W, PERSON_W, PERSON_W])
    t.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), font),
                ("FONTSIZE", (0, 0), (-1, 0), 8),
                ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
                ("TEXTCOLOR", (0, 0), (-1, 0), BRAND),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.4, GRID),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return t


def question_block(qid: str, text: str, opts: str, s, font: str):
    q = Paragraph(f"<b>{qid}.</b> {text}", s["q"])
    headers = ["Person 1", "Person 2", "Person 3"]
    row = [answer_cell(opts, s) for _ in range(3)]
    t = three_col_table(headers, row, font)
    return KeepTogether([Spacer(1, 1.5 * mm), q, Spacer(1, 1 * mm), t])


def draw_header_footer(canvas, doc):
    canvas.saveState()
    font = doc.survey_font

    canvas.setFillColor(BRAND)
    canvas.rect(0, PAGE_H - 12 * mm, PAGE_W, 12 * mm, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont(font, 9)
    canvas.drawString(SIDE, PAGE_H - 7.5 * mm, "FraudLens")
    canvas.setFont(font, 7)
    canvas.drawRightString(
        PAGE_W - SIDE,
        PAGE_H - 7.5 * mm,
        "Real-time fraud decisions for mobile money",
    )

    canvas.setStrokeColor(GRID)
    canvas.setLineWidth(0.4)
    y = 10 * mm
    canvas.line(SIDE, y + 4 * mm, PAGE_W - SIDE, y + 4 * mm)
    canvas.setFillColor(MUTED)
    canvas.setFont(font, 6.5)
    canvas.drawString(
        SIDE,
        y,
        "FraudLens · student research project · anonymous · not an MFS provider or Bangladesh Bank",
    )
    canvas.drawRightString(PAGE_W - SIDE, y, f"Page {doc.page}")
    canvas.restoreState()


def build():
    font = register_font()
    s = styles(font)
    OUT.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(OUT),
        pagesize=A4,
        leftMargin=SIDE,
        rightMargin=SIDE,
        topMargin=18 * mm,
        bottomMargin=16 * mm,
        title="FraudLens customer survey — 3 respondents",
        author="FraudLens",
        subject="Validation survey: scam experience and warn / step-up / hold reactions",
    )
    doc.survey_font = font

    story = []
    story.append(Paragraph("FraudLens", s["brand"]))
    story.append(
        Paragraph(
            "Stop the scam before the money moves — real-time fraud decisions for mobile money, "
            "explained in Bangla and reviewed by people.",
            s["tagline"],
        )
    )
    story.append(Paragraph("Customer validation survey (3 respondents per sheet)", s["title"]))
    story.append(
        Paragraph(
            "<b>System:</b> FraudLens &nbsp;|&nbsp; <b>Purpose:</b> measure real scam exposure and "
            "acceptance of warn / step-up / hold interventions &nbsp;|&nbsp; "
            "<b>Duration:</b> ~8 minutes &nbsp;|&nbsp; <b>Who:</b> adult (18+) personal MFS users "
            "(bKash, Nagad, Rocket, etc.)",
            s["note"],
        )
    )
    story.append(
        Paragraph(
            "<b>Never record:</b> name, phone, wallet number, PIN, OTP, NID, or account balance. "
            "If someone starts to say a PIN/OTP, stop them. Answers are anonymous and used only in aggregate.",
            s["note"],
        )
    )

    story.append(Paragraph("Profile (enumerator)", s["h"]))
    profile_cell = (
        "<b>respondent_id</b><br/>_______________<br/><br/>"
        "<b>area</b> ☐ urban ☐ rural<br/><br/>"
        "<b>consent</b> ☐ yes ☐ no"
    )
    profile = three_col_table(
        ["Person 1", "Person 2", "Person 3"],
        [Paragraph(profile_cell, s["cell"]) for _ in range(3)],
        font,
    )
    story.append(profile)

    story.append(Paragraph("Consent (read aloud before any question)", s["h"]))
    story.append(
        Paragraph(
            "আসসালামু আলাইকুম। আমরা <b>FraudLens</b> নামে একটি ছাত্র প্রকল্পের পক্ষ থেকে এসেছি। "
            "আমরা মোবাইল ব্যাংকিং (যেমন বিকাশ, নগদ, রকেট) প্রতারণা কমানোর একটি পদ্ধতি নিয়ে কাজ করছি। "
            "এতে প্রায় ৮ মিনিট লাগবে। আমরা নাম, ফোন, অ্যাকাউন্ট, পিন বা ওটিপি জানতে চাইব না। "
            "উত্তর বেনামে রাখা হবে। অংশ নেওয়া আপনার ইচ্ছা। আপনি কি অংশ নিতে রাজি আছেন?",
            s["body"],
        )
    )
    story.append(
        Paragraph(
            "If consent = no: thank them and stop. If they lost money: mention provider helpline "
            "(resolve within 10 working days) and Bangladesh Bank CIPC.",
            s["note"],
        )
    )

    story.append(Paragraph("Part A — scam experience (last 12 months)", s["h"]))

    story.append(
        question_block(
            "Q1",
            "Has anyone called, texted or messaged you trying to get money or a PIN/OTP "
            "from your mobile wallet? / কেউ কি কল/মেসেজ করে টাকা বা PIN/OTP নিতে চেয়েছে?",
            "☐ yes &nbsp;&nbsp; ☐ no",
            s,
            font,
        )
    )
    story.append(
        question_block(
            "Q2",
            "Did you lose money to a scam of this kind? / এই ধরনের প্রতারণায় টাকা গেছে কি?",
            "☐ yes &nbsp;&nbsp; ☐ no<br/><font size='6'>(if no → skip Q3–Q6)</font>",
            s,
            font,
        )
    )
    story.append(
        question_block(
            "Q3",
            "(If yes) Which is closest to what happened? / কোনটি সবচেয়ে কাছাকাছি?",
            "☐ impersonation<br/>☐ lottery_fee<br/>☐ wrong_send<br/>"
            "☐ investment<br/>☐ account_takeover<br/>☐ other",
            s,
            font,
        )
    )
    story.append(
        question_block(
            "Q4",
            "(If yes) Roughly how much did you lose, in taka? / আনুমানিক কত টাকা গেছে?",
            "__________ BDT",
            s,
            font,
        )
    )
    story.append(
        question_block(
            "Q5",
            "(If yes) Who did you tell? / কাকে জানিয়েছেন?",
            "☐ nobody<br/>☐ provider<br/>☐ police<br/>☐ cipc<br/>☐ several",
            s,
            font,
        )
    )
    story.append(
        question_block(
            "Q6",
            "(If yes) Did you get any of it back? / কিছু টাকা ফিরে পেয়েছেন?",
            "☐ none &nbsp; ☐ partial &nbsp; ☐ full",
            s,
            font,
        )
    )

    story.append(
        Paragraph(
            "Part B — reactions to the three FraudLens screens "
            "(1 = strongly disagree … 5 = strongly agree)",
            s["h"],
        )
    )
    story.append(
        Paragraph(
            "<b>Warn (FraudLens):</b> “Before you send Tk 8,000: this number was opened 2 days ago "
            "and has received money from many people today… Do you know this person?” "
            "[Send anyway] [Cancel]",
            s["note"],
        )
    )
    story.append(
        question_block(
            "Q7",
            "The warning would make me stop and check before sending. / সতর্কবার্তা দেখলে থামব ও যাচাই করব।",
            "☐1 &nbsp; ☐2 &nbsp; ☐3 &nbsp; ☐4 &nbsp; ☐5",
            s,
            font,
        )
    )
    story.append(
        Paragraph(
            "<b>Step-up (FraudLens):</b> “This payment looks like a common scam, so it is paused "
            "for 30 minutes. Enter your PIN again… after the pause it goes through unless you cancel.”",
            s["note"],
        )
    )
    story.append(
        question_block(
            "Q8",
            "Confirming with my PIN again and waiting 30 minutes would be acceptable. / "
            "আবার PIN + ৩০ মিনিট অপেক্ষা গ্রহণযোগ্য।",
            "☐1 &nbsp; ☐2 &nbsp; ☐3 &nbsp; ☐4 &nbsp; ☐5",
            s,
            font,
        )
    )
    story.append(
        Paragraph(
            "<b>Hold (FraudLens):</b> “We are holding this Tk 15,000 transfer for up to 30 minutes "
            "so a person can check it. You can cancel now, or call your provider’s helpline.”",
            s["note"],
        )
    )
    story.append(
        question_block(
            "Q9",
            "Having a payment held for a while would be acceptable if it protects me. / "
            "হোল্ড করা গ্রহণযোগ্য যদি সুরক্ষা দেয়।",
            "☐1 &nbsp; ☐2 &nbsp; ☐3 &nbsp; ☐4 &nbsp; ☐5",
            s,
            font,
        )
    )
    story.append(
        question_block(
            "Q10",
            "Longest hold you would accept (minutes; 0 = never)? / সবচেয়ে বেশি কত মিনিট হোল্ড মানবেন?",
            "__________ min",
            s,
            font,
        )
    )
    story.append(
        question_block(
            "Q11",
            "How many false alarms per year before you stop trusting it? / "
            "বছরে কতবার ভুল সতর্কতায় আর বিশ্বাস করবেন না?",
            "__________ / year",
            s,
            font,
        )
    )
    story.append(
        question_block(
            "Q12",
            "If your wallet showed screens like these, would you trust it more, the same, or less?",
            "☐ more &nbsp; ☐ same &nbsp; ☐ less",
            s,
            font,
        )
    )
    story.append(
        question_block(
            "Q13",
            "Which language should these screens be in? / কোন ভাষায় হওয়া উচিত?",
            "☐ bangla &nbsp; ☐ english &nbsp; ☐ both",
            s,
            font,
        )
    )
    story.append(
        question_block(
            "Q14",
            "Anything else you want to tell us? / আর কিছু বলতে চান?",
            "_______________________________<br/>_______________________________",
            s,
            font,
        )
    )

    story.append(Paragraph("Extra notes (optional)", s["h"]))
    notes = three_col_table(
        ["Person 1", "Person 2", "Person 3"],
        [
            Paragraph("<br/><br/><br/><br/>", s["cell"]),
            Paragraph("<br/><br/><br/><br/>", s["cell"]),
            Paragraph("<br/><br/><br/><br/>", s["cell"]),
        ],
        font,
    )
    story.append(notes)
    story.append(Spacer(1, 3 * mm))
    story.append(
        Paragraph(
            "<b>FraudLens analysis:</b> after fieldwork, copy answers into CSV columns from "
            "docs/validation/customer_survey.md, then run "
            "<font face='Courier' size='6.5'>cd backend &amp;&amp; uv run python -m "
            "fraudlens.validation.survey_analysis responses.csv --by area</font>",
            s["note"],
        )
    )

    doc.build(story, onFirstPage=draw_header_footer, onLaterPages=draw_header_footer)
    print(OUT)


if __name__ == "__main__":
    build()
