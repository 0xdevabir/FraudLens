#!/usr/bin/env python3
"""Build a printable Upay-branded FraudLens survey form (3 respondents)."""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1] / "printables" / "customer_survey_3_respondents.pdf"
LOGO = ROOT / "frontend" / "app" / "(console)" / "phone" / "upay-logo.png"
FONT_CANDIDATES = [
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    Path("/Library/Fonts/Arial Unicode.ttf"),
]

PAGE_W, PAGE_H = A4
SIDE = 14 * mm
CONTENT_W = PAGE_W - 2 * SIDE
PERSON_W = CONTENT_W / 3

# upay colours from frontend/app/globals.css (measured from the app icon)
UPAY_YELLOW = colors.HexColor("#ffd600")
UPAY_BLUE = colors.HexColor("#0054a5")
HEADER_BG = colors.HexColor("#E8F1FA")  # light blue wash
GRID = colors.HexColor("#8A8A8A")
MUTED = colors.HexColor("#444444")
BLACK = colors.black


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
            fontSize=15,
            leading=18,
            textColor=UPAY_BLUE,
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
            fontSize=11.5,
            leading=14,
            alignment=TA_LEFT,
            textColor=BLACK,
            spaceBefore=1.5 * mm,
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
            textColor=UPAY_BLUE,
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
                ("BACKGROUND", (0, 0), (-1, 0), UPAY_YELLOW),
                ("TEXTCOLOR", (0, 0), (-1, 0), BLACK),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.4, UPAY_BLUE),
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

    # upay yellow status/title bar (matches phone demo)
    canvas.setFillColor(UPAY_YELLOW)
    canvas.rect(0, PAGE_H - 14 * mm, PAGE_W, 14 * mm, fill=1, stroke=0)

    # logo in yellow bar
    if LOGO.is_file():
        logo_size = 10 * mm
        canvas.drawImage(
            str(LOGO),
            SIDE,
            PAGE_H - 12.5 * mm,
            width=logo_size,
            height=logo_size,
            mask="auto",
            preserveAspectRatio=True,
        )
        text_x = SIDE + logo_size + 3 * mm
    else:
        text_x = SIDE

    canvas.setFillColor(BLACK)
    canvas.setFont(font, 11)
    canvas.drawString(text_x, PAGE_H - 6.5 * mm, "upay")
    canvas.setFont(font, 7)
    canvas.setFillColor(UPAY_BLUE)
    canvas.drawString(text_x, PAGE_H - 10.5 * mm, "Customer validation survey")
    canvas.setFillColor(BLACK)
    canvas.setFont(font, 7)
    canvas.drawRightString(PAGE_W - SIDE, PAGE_H - 8 * mm, "উপায়")

    # blue accent line under bar
    canvas.setStrokeColor(UPAY_BLUE)
    canvas.setLineWidth(1.2)
    canvas.line(0, PAGE_H - 14 * mm, PAGE_W, PAGE_H - 14 * mm)

    # Footer
    canvas.setStrokeColor(UPAY_BLUE)
    canvas.setLineWidth(0.6)
    y = 10 * mm
    canvas.line(SIDE, y + 4.5 * mm, PAGE_W - SIDE, y + 4.5 * mm)
    canvas.setFillColor(MUTED)
    canvas.setFont(font, 6)
    canvas.drawString(
        SIDE,
        y,
        "upay survey · FraudLens student research · anonymous · not Bangladesh Bank",
    )
    canvas.setFillColor(UPAY_BLUE)
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
        topMargin=20 * mm,
        bottomMargin=16 * mm,
        title="upay customer survey — 3 respondents (FraudLens validation)",
        author="FraudLens / upay validation",
        subject="upay customer survey: scam experience and warn / step-up / hold reactions",
    )
    doc.survey_font = font

    story = []

    # Identity block with logo
    if LOGO.is_file():
        logo = Image(str(LOGO), width=16 * mm, height=16 * mm)
        identity = Table(
            [
                [
                    logo,
                    [
                        Paragraph("<b>upay</b> · উপায়", s["brand"]),
                        Paragraph(
                            "FraudLens validation survey for <b>upay</b> customers only — "
                            "real-time fraud decisions for mobile money.",
                            s["tagline"],
                        ),
                    ],
                ]
            ],
            colWidths=[20 * mm, CONTENT_W - 20 * mm],
        )
        identity.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                    ("BACKGROUND", (0, 0), (-1, -1), HEADER_BG),
                    ("BOX", (0, 0), (-1, -1), 0.8, UPAY_BLUE),
                    ("LEFTPADDING", (0, 0), (0, 0), 3),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        story.append(identity)
    else:
        story.append(Paragraph("upay · উপায়", s["brand"]))

    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph("Customer validation survey (3 respondents per sheet)", s["title"]))
    story.append(
        Paragraph(
            "<b>Partner:</b> upay (UCB Fintech) &nbsp;|&nbsp; "
            "<b>System:</b> FraudLens &nbsp;|&nbsp; "
            "<b>Purpose:</b> scam exposure + acceptance of warn / step-up / hold &nbsp;|&nbsp; "
            "<b>Duration:</b> ~8 minutes &nbsp;|&nbsp; "
            "<b>Who:</b> adult (18+) personal <b>upay</b> account holders only",
            s["note"],
        )
    )
    story.append(
        Paragraph(
            "<b>Never record:</b> name, phone, upay wallet number, PIN, OTP, NID, or balance. "
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
    story.append(
        three_col_table(
            ["Person 1", "Person 2", "Person 3"],
            [Paragraph(profile_cell, s["cell"]) for _ in range(3)],
            font,
        )
    )

    story.append(Paragraph("Consent (read aloud before any question)", s["h"]))
    story.append(
        Paragraph(
            "আসসালামু আলাইকুম। আমরা <b>upay</b> গ্রাহকদের জন্য FraudLens নামে একটি ছাত্র প্রকল্পের পক্ষ থেকে এসেছি। "
            "আমরা <b>upay</b> মোবাইল ব্যাংকিং প্রতারণা কমানোর একটি পদ্ধতি নিয়ে কাজ করছি। "
            "এতে প্রায় ৮ মিনিট লাগবে। আমরা নাম, ফোন, upay অ্যাকাউন্ট, পিন বা ওটিপি জানতে চাইব না। "
            "উত্তর বেনামে রাখা হবে। অংশ নেওয়া আপনার ইচ্ছা। আপনি কি অংশ নিতে রাজি আছেন?",
            s["body"],
        )
    )
    story.append(
        Paragraph(
            "If consent = no: thank them and stop. If they lost money: mention the upay helpline "
            "(providers must resolve disputes within 10 working days) and Bangladesh Bank CIPC.",
            s["note"],
        )
    )

    story.append(Paragraph("Part A — scam experience with upay (last 12 months)", s["h"]))

    story.append(
        question_block(
            "Q1",
            "Has anyone called, texted or messaged you trying to get money or a PIN/OTP "
            "from your <b>upay</b> wallet? / কেউ কি কল/মেসেজ করে আপনার <b>upay</b> থেকে টাকা বা PIN/OTP নিতে চেয়েছে?",
            "☐ yes &nbsp;&nbsp; ☐ no",
            s,
            font,
        )
    )
    story.append(
        question_block(
            "Q2",
            "Did you lose money from your <b>upay</b> account to a scam of this kind? / "
            "এই ধরনের প্রতারণায় আপনার <b>upay</b> থেকে টাকা গেছে কি?",
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
            "☐ nobody<br/>☐ upay / provider<br/>☐ police<br/>☐ cipc<br/>☐ several",
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
            "Part B — reactions to the three upay / FraudLens screens "
            "(1 = strongly disagree … 5 = strongly agree)",
            s["h"],
        )
    )
    story.append(
        Paragraph(
            "<b>Warn (upay):</b> “Before you send Tk 8,000: this number was opened 2 days ago "
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
            "<b>Step-up (upay):</b> “This payment looks like a common scam, so it is paused "
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
            "<b>Hold (upay):</b> “We are holding this Tk 15,000 transfer for up to 30 minutes "
            "so a person can check it. You can cancel now, or call the upay helpline.”",
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
            "How many false alarms per year before you stop trusting <b>upay</b>? / "
            "বছরে কতবার ভুল সতর্কতায় আর <b>upay</b>-তে বিশ্বাস করবেন না?",
            "__________ / year",
            s,
            font,
        )
    )
    story.append(
        question_block(
            "Q12",
            "If your <b>upay</b> app showed screens like these, would you trust it more, the same, or less?",
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
            "Anything else you want to tell us about upay safety? / upay নিরাপত্তা নিয়ে আর কিছু?",
            "_______________________________<br/>_______________________________",
            s,
            font,
        )
    )

    story.append(Paragraph("Extra notes (optional)", s["h"]))
    story.append(
        three_col_table(
            ["Person 1", "Person 2", "Person 3"],
            [
                Paragraph("<br/><br/><br/><br/>", s["cell"]),
                Paragraph("<br/><br/><br/><br/>", s["cell"]),
                Paragraph("<br/><br/><br/><br/>", s["cell"]),
            ],
            font,
        )
    )
    story.append(Spacer(1, 3 * mm))
    story.append(
        Paragraph(
            "<b>Attribution:</b> upay name and logo belong to UCB Fintech Company Limited. "
            "This form is a student research mock for FraudLens validation with upay customers — "
            "not an official upay document. "
            "After fieldwork: "
            "<font face='Courier' size='6.5'>cd backend &amp;&amp; uv run python -m "
            "fraudlens.validation.survey_analysis responses.csv --by area</font>",
            s["note"],
        )
    )

    doc.build(story, onFirstPage=draw_header_footer, onLaterPages=draw_header_footer)
    print(OUT)


if __name__ == "__main__":
    build()
