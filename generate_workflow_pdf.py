from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether
)
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import Flowable
import textwrap
import os

# ── Palette ────────────────────────────────────────────────────────────────────
BLACK   = colors.HexColor('#0A0A0A')
YELLOW  = colors.HexColor('#FFD600')
YELLOW2 = colors.HexColor('#FFC107')
YELLOW_DIM = colors.HexColor('#FFF176')
WHITE   = colors.white
OFFWHITE= colors.HexColor('#FAFAFA')
GREY1   = colors.HexColor('#1A1A1A')   # near-black bg
GREY2   = colors.HexColor('#2B2B2B')   # card bg
GREY3   = colors.HexColor('#3D3D3D')   # border/secondary
GREY_MID= colors.HexColor('#888888')   # muted text
W, H    = A4  # 595 x 842 pt

# ── Styles ─────────────────────────────────────────────────────────────────────
def make_styles():
    return {
        'cover_tag':  ParagraphStyle('cover_tag',  fontName='Helvetica',      fontSize=8,  textColor=GREY_MID,  letterSpacing=3, alignment=TA_CENTER, leading=12),
        'cover_track':ParagraphStyle('cover_track',fontName='Helvetica-Bold', fontSize=9,  textColor=YELLOW,    letterSpacing=2, alignment=TA_CENTER, leading=14),
        'cover_title':ParagraphStyle('cover_title',fontName='Helvetica-Bold', fontSize=40, textColor=WHITE,     alignment=TA_CENTER, leading=46, spaceAfter=4),
        'cover_sub':  ParagraphStyle('cover_sub',  fontName='Helvetica-Bold', fontSize=40, textColor=YELLOW,    alignment=TA_CENTER, leading=46, spaceAfter=16),
        'cover_desc': ParagraphStyle('cover_desc', fontName='Helvetica',      fontSize=13, textColor=OFFWHITE,  alignment=TA_CENTER, leading=21, spaceAfter=28),
        'cover_stat_val': ParagraphStyle('sv', fontName='Helvetica-Bold', fontSize=32, textColor=YELLOW, alignment=TA_CENTER, leading=36),
        'cover_stat_lab': ParagraphStyle('sl', fontName='Helvetica',      fontSize=8,  textColor=GREY_MID, alignment=TA_CENTER, leading=12, letterSpacing=1),

        'section_tag':ParagraphStyle('section_tag', fontName='Helvetica-Bold', fontSize=8,  textColor=YELLOW, letterSpacing=3, alignment=TA_LEFT, leading=12, spaceAfter=4),
        'h1':   ParagraphStyle('h1',   fontName='Helvetica-Bold', fontSize=24, textColor=WHITE,   leading=30, spaceBefore=0, spaceAfter=10),
        'h2':   ParagraphStyle('h2',   fontName='Helvetica-Bold', fontSize=15, textColor=YELLOW,  leading=20, spaceBefore=14, spaceAfter=6),
        'h3':   ParagraphStyle('h3',   fontName='Helvetica-Bold', fontSize=11, textColor=WHITE,   leading=15, spaceBefore=8, spaceAfter=4),
        'body': ParagraphStyle('body', fontName='Helvetica',       fontSize=10, textColor=OFFWHITE,leading=16, spaceBefore=2, spaceAfter=4),
        'body_sm': ParagraphStyle('bsm', fontName='Helvetica',    fontSize=9,  textColor=GREY_MID,leading=13, spaceAfter=2),
        'bullet':ParagraphStyle('bullet',fontName='Helvetica',    fontSize=10, textColor=OFFWHITE,leading=15, leftIndent=14, spaceAfter=3),
        'mono': ParagraphStyle('mono',  fontName='Courier',        fontSize=9,  textColor=YELLOW,  leading=13, spaceAfter=2),
        'label':ParagraphStyle('label', fontName='Helvetica-Bold', fontSize=8,  textColor=YELLOW,  letterSpacing=2, leading=12, spaceAfter=2),
        'answer': ParagraphStyle('answer', fontName='Helvetica',   fontSize=10, textColor=OFFWHITE,leading=16, leftIndent=12, spaceAfter=6),
        'tbl_hd': ParagraphStyle('tbl_hd', fontName='Helvetica-Bold', fontSize=9, textColor=BLACK, leading=13),
        'tbl_cell': ParagraphStyle('tbl_cell', fontName='Helvetica', fontSize=9, textColor=GREY2, leading=13),
        'footer': ParagraphStyle('footer', fontName='Helvetica', fontSize=8, textColor=GREY3, alignment=TA_CENTER, leading=11),
        'qa_q': ParagraphStyle('qa_q', fontName='Helvetica-Bold', fontSize=11, textColor=YELLOW, leading=16, spaceBefore=10, spaceAfter=4),
        'qa_a': ParagraphStyle('qa_a', fontName='Helvetica', fontSize=10, textColor=OFFWHITE, leading=16, leftIndent=10, spaceAfter=8),
    }

S = make_styles()

# ── Custom Flowables ───────────────────────────────────────────────────────────
class YellowRule(Flowable):
    def __init__(self, width=None, thickness=2, color=YELLOW, spaceAfter=8):
        super().__init__()
        self._width = width
        self.thickness = thickness
        self.color = color
        self.spaceAfter = spaceAfter
        self.height = thickness + spaceAfter

    def draw(self):
        self.canv.setFillColor(self.color)
        self.canv.rect(0, self.spaceAfter, self._width or self.canv._pagesize[0] - 60*mm, self.thickness, fill=1, stroke=0)

    def wrap(self, avW, avH):
        self._width = self._width or avW
        return (avW, self.height)


class DarkCard(Flowable):
    """A dark rounded card background with optional yellow left border."""
    def __init__(self, content_height, bg=GREY2, border_color=YELLOW, border_left=True):
        super().__init__()
        self.content_height = content_height
        self.bg = bg
        self.border_color = border_color
        self.border_left = border_left
        self.width = 0

    def wrap(self, avW, avH):
        self.width = avW
        return (avW, self.content_height + 16)

    def draw(self):
        c = self.canv
        w, h = self.width, self.content_height + 16
        c.setFillColor(self.bg)
        c.roundRect(0, 0, w, h, 4, fill=1, stroke=0)
        if self.border_left:
            c.setFillColor(self.border_color)
            c.rect(0, 0, 3, h, fill=1, stroke=0)


class StatBar(Flowable):
    """4-stat horizontal bar."""
    def __init__(self, stats):
        super().__init__()
        self.stats = stats  # [(value, label), ...]
        self.height = 70

    def wrap(self, avW, avH):
        self.width = avW
        return (avW, self.height)

    def draw(self):
        c = self.canv
        n = len(self.stats)
        w = self.width / n
        for i, (val, lab) in enumerate(self.stats):
            x = i * w
            c.setFillColor(GREY2)
            c.roundRect(x + 4, 4, w - 8, self.height - 8, 4, fill=1, stroke=0)
            c.setFont('Helvetica-Bold', 26)
            c.setFillColor(YELLOW)
            c.drawCentredString(x + w/2, self.height - 34, val)
            c.setFont('Helvetica', 7)
            c.setFillColor(GREY_MID)
            for j, part in enumerate(lab.split('\n')):
                c.drawCentredString(x + w/2, self.height - 48 - j*9, part.upper())


class FlowDiagram(Flowable):
    """Journey flow diagram."""
    def __init__(self, steps, color=YELLOW, height=54):
        super().__init__()
        self.steps = steps
        self.color = color
        self.height = height
        self.width = 0

    def wrap(self, avW, avH):
        self.width = avW
        return (avW, self.height)

    def draw(self):
        c = self.canv
        n = len(self.steps)
        step_w = self.width / n
        cy = self.height / 2

        for i, (icon, text) in enumerate(self.steps):
            x = i * step_w + step_w/2
            # Circle
            c.setFillColor(self.color)
            c.circle(x, cy + 8, 10, fill=1, stroke=0)
            c.setFont('Helvetica-Bold', 9)
            c.setFillColor(BLACK)
            c.drawCentredString(x, cy + 5, icon)
            # Text below
            c.setFont('Helvetica', 7)
            c.setFillColor(OFFWHITE)
            for j, line in enumerate(text.split('\n')):
                c.drawCentredString(x, cy - 10 - j*9, line)
            # Arrow
            if i < n - 1:
                ax = x + step_w/2
                c.setStrokeColor(GREY3)
                c.setLineWidth(1)
                c.line(x + 12, cy + 8, ax - 12, cy + 8)
                c.setFillColor(GREY3)
                c.polygon([ax-12, cy+11, ax-12, cy+5, ax-4, cy+8], fill=1, stroke=0)


class ArchTable(Flowable):
    """Data flow table drawn directly."""
    def __init__(self, rows):
        super().__init__()
        self.rows = rows  # [(from, to, what, protocol)]
        self.row_h = 26
        self.height = self.row_h * (len(rows) + 1) + 4

    def wrap(self, avW, avH):
        self.width = avW
        return (avW, self.height)

    def draw(self):
        c = self.canv
        cols = [80, 80, 220, 75]
        headers = ['FROM', 'TO', 'WHAT IS SENT', 'PROTOCOL']
        total = sum(cols)
        scale = self.width / total
        scaled = [int(x*scale) for x in cols]

        # Header row
        c.setFillColor(YELLOW)
        c.rect(0, self.height - self.row_h, self.width, self.row_h, fill=1, stroke=0)
        x = 0
        for i, (h, w) in enumerate(zip(headers, scaled)):
            c.setFont('Helvetica-Bold', 8)
            c.setFillColor(BLACK)
            c.drawString(x + 6, self.height - self.row_h + 9, h)
            x += w

        # Data rows
        for ri, row in enumerate(self.rows):
            y = self.height - self.row_h * (ri + 2)
            bg = GREY1 if ri % 2 == 0 else GREY2
            c.setFillColor(bg)
            c.rect(0, y, self.width, self.row_h, fill=1, stroke=0)

            proto_colors = {
                'HTTP POST': colors.HexColor('#FFD600'),
                'WebSocket': colors.HexColor('#FFC107'),
                'HTTP Response': colors.HexColor('#FFF176'),
            }

            x = 0
            for ci, (cell, cw) in enumerate(zip(row, scaled)):
                if ci == 3:  # Protocol badge
                    pc = proto_colors.get(cell, YELLOW)
                    c.setFillColor(pc)
                    c.roundRect(x + 4, y + 6, cw - 8, 14, 3, fill=1, stroke=0)
                    c.setFont('Helvetica-Bold', 7)
                    c.setFillColor(BLACK)
                    c.drawCentredString(x + cw/2, y + 10, cell)
                else:
                    c.setFont('Helvetica', 8)
                    c.setFillColor(OFFWHITE)
                    c.drawString(x + 6, y + 9, str(cell))
                x += cw

            # Bottom border
            c.setStrokeColor(GREY3)
            c.setLineWidth(0.5)
            c.line(0, y, self.width, y)


class StateMachine(Flowable):
    """OCPP state machine diagram."""
    def __init__(self):
        super().__init__()
        self.height = 210

    def wrap(self, avW, avH):
        self.width = avW
        return (avW, self.height)

    def draw(self):
        c = self.canv
        w = self.width
        # Background
        c.setFillColor(GREY1)
        c.roundRect(0, 0, w, self.height, 6, fill=1, stroke=0)

        def box(label, x, y, bw=100, bh=26, color=YELLOW, tc=BLACK):
            c.setFillColor(color)
            c.roundRect(x - bw/2, y - bh/2, bw, bh, 4, fill=1, stroke=0)
            c.setFont('Helvetica-Bold', 9)
            c.setFillColor(tc)
            c.drawCentredString(x, y - 3, label)

        def arrow(x1, y1, x2, y2, label=''):
            c.setStrokeColor(GREY_MID)
            c.setLineWidth(1)
            c.line(x1, y1, x2, y2)
            if label:
                mx, my = (x1+x2)/2, (y1+y2)/2
                c.setFont('Helvetica', 7)
                c.setFillColor(GREY_MID)
                c.drawCentredString(mx, my + 3, label)

        cx = w / 2
        # States
        box('System Start', cx, self.height - 18, bw=100, bh=20, color=GREY3, tc=WHITE)
        arrow(cx, self.height - 28, cx, self.height - 56)

        box('AVAILABLE', cx, self.height - 68, bw=110, bh=26, color=YELLOW)
        # Arrow down to Reserved
        arrow(cx, self.height - 81, cx, self.height - 112, 'Driver clicks Reserve')
        box('RESERVED', cx, self.height - 124, bw=110, bh=26, color=colors.HexColor('#444400'), tc=YELLOW)

        # Wrong PIN -> AccessDenied
        arrow(cx - 55, self.height - 124, cx - 110, self.height - 152, 'Wrong PIN')
        box('ACCESS DENIED', cx - 115, self.height - 164, bw=90, bh=22, color=GREY3, tc=WHITE)
        # Try again arrow back
        arrow(cx - 90, self.height - 164, cx - 55, self.height - 136, 'Retry')

        # Correct PIN -> Unlocked
        arrow(cx + 55, self.height - 124, cx + 110, self.height - 152, 'Correct PIN')
        box('UNLOCKED', cx + 115, self.height - 164, bw=90, bh=22, color=GREY2, tc=YELLOW)
        arrow(cx + 115, self.height - 175, cx + 80, self.height - 192, 'Relay closes')

        # Charging
        box('CHARGING', cx, self.height - 198, bw=110, bh=26, color=YELLOW, tc=BLACK)
        # Timer expires back to Available
        arrow(cx - 55, self.height - 136, cx - 150, self.height - 136)
        c.setStrokeColor(GREY_MID)
        c.setLineWidth(1)
        c.line(cx - 150, self.height - 136, cx - 150, self.height - 68)
        arrow(cx - 150, self.height - 68, cx - 55, self.height - 68, '30min expire')

        # SoC 80% -> Finishing
        arrow(cx + 55, self.height - 198, cx + 115, self.height - 198, 'SoC 80%')
        box('FINISHING', cx + 150, self.height - 198, bw=90, bh=22, color=GREY2, tc=YELLOW2)


class CompareTable(Flowable):
    """Competitive comparison table."""
    def __init__(self, rows):
        super().__init__()
        self.rows = rows
        self.row_h = 28
        self.height = self.row_h * (len(rows) + 1)

    def wrap(self, avW, avH):
        self.width = avW
        return (avW, self.height)

    def draw(self):
        c = self.canv
        half = self.width / 2

        # Headers
        c.setFillColor(GREY3)
        c.rect(0, self.height - self.row_h, half - 2, self.row_h, fill=1, stroke=0)
        c.setFillColor(YELLOW)
        c.rect(half + 2, self.height - self.row_h, half - 2, self.row_h, fill=1, stroke=0)

        c.setFont('Helvetica-Bold', 9)
        c.setFillColor(WHITE)
        c.drawCentredString(half/2, self.height - self.row_h + 9, 'OTHER TEAMS')
        c.setFillColor(BLACK)
        c.drawCentredString(half + half/2, self.height - self.row_h + 9, 'SMARTRESERVE')

        for i, (col1, col2) in enumerate(self.rows):
            y = self.height - self.row_h * (i + 2)
            bg1 = GREY1 if i % 2 == 0 else GREY2
            c.setFillColor(bg1)
            c.rect(0, y, half - 2, self.row_h, fill=1, stroke=0)
            c.setFillColor(colors.HexColor('#1A1500') if i % 2 == 0 else colors.HexColor('#2B2500'))
            c.rect(half + 2, y, half - 2, self.row_h, fill=1, stroke=0)

            c.setFont('Helvetica', 8)
            c.setFillColor(GREY_MID)
            # Wrap text
            words1 = col1
            c.drawString(8, y + 9, words1[:55])
            c.setFillColor(OFFWHITE)
            c.drawString(half + 10, y + 9, col2[:55])

            c.setStrokeColor(GREY3)
            c.setLineWidth(0.3)
            c.line(0, y, self.width, y)


# ── Page Template ──────────────────────────────────────────────────────────────
class SmartReservePDF:
    def __init__(self, path):
        self.path = path
        self.c = canvas.Canvas(path, pagesize=A4)
        self.page_num = 0

    def new_page(self):
        if self.page_num > 0:
            self.c.showPage()
        self.page_num += 1
        # Dark background
        self.c.setFillColor(GREY1)
        self.c.rect(0, 0, W, H, fill=1, stroke=0)
        # Yellow top stripe
        self.c.setFillColor(YELLOW)
        self.c.rect(0, H - 4, W, 4, fill=1, stroke=0)
        # Footer
        if self.page_num > 1:
            self.c.setFont('Helvetica', 7)
            self.c.setFillColor(GREY3)
            self.c.drawString(30, 16, 'HYUNDAI SmartReserve  ·  CoE Mobility Innovation Ideathon 2026  ·  Track B')
            self.c.setFillColor(YELLOW)
            self.c.drawRightString(W - 30, 16, f'{self.page_num}')

    def section_header(self, tag, title, y):
        self.c.setFont('Helvetica-Bold', 7)
        self.c.setFillColor(YELLOW)
        self.c.drawString(30, y, tag.upper())
        self.c.setFont('Helvetica-Bold', 22)
        self.c.setFillColor(WHITE)
        self.c.drawString(30, y - 22, title)
        self.c.setFillColor(YELLOW)
        self.c.rect(30, y - 28, 40, 2, fill=1, stroke=0)
        return y - 44

    def text_block(self, text, x, y, width, font='Helvetica', size=10, color=OFFWHITE, leading=15):
        self.c.setFont(font, size)
        self.c.setFillColor(color)
        wrapped = textwrap.wrap(text, width=int(width / (size * 0.55)))
        for line in wrapped:
            self.c.drawString(x, y, line)
            y -= leading
        return y

    def yellow_box(self, x, y, w, h, label, value, sub=''):
        self.c.setFillColor(GREY2)
        self.c.roundRect(x, y, w, h, 4, fill=1, stroke=0)
        self.c.setFillColor(YELLOW)
        self.c.rect(x, y + h - 2, w, 2, fill=1, stroke=0)
        self.c.setFont('Helvetica-Bold', 7)
        self.c.setFillColor(YELLOW)
        self.c.drawString(x + 8, y + h - 14, label.upper())
        self.c.setFont('Helvetica-Bold', 20)
        self.c.setFillColor(WHITE)
        self.c.drawString(x + 8, y + h - 36, value)
        if sub:
            self.c.setFont('Helvetica', 8)
            self.c.setFillColor(GREY_MID)
            self.c.drawString(x + 8, y + 8, sub)

    def pill(self, x, y, text, bg=YELLOW, tc=BLACK):
        self.c.setFont('Helvetica-Bold', 8)
        tw = self.c.stringWidth(text, 'Helvetica-Bold', 8)
        pw = tw + 16
        self.c.setFillColor(bg)
        self.c.roundRect(x, y - 2, pw, 14, 7, fill=1, stroke=0)
        self.c.setFillColor(tc)
        self.c.drawString(x + 8, y + 1, text)
        return x + pw + 8

    def save(self):
        self.c.save()


# ── Build PDF ──────────────────────────────────────────────────────────────────
def build():
    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hyundai_workflow.pdf')
    pdf = SmartReservePDF(output_path)

    # ── COVER PAGE ────────────────────────────────────────────────────────────
    pdf.new_page()

    # Full dark bg already drawn. Add gradient feel with lighter center
    pdf.c.setFillColor(colors.HexColor('#141414'))
    pdf.c.roundRect(60, 180, W - 120, 480, 12, fill=1, stroke=0)

    # Ideathon tag
    pdf.c.setFont('Helvetica-Bold', 7)
    pdf.c.setFillColor(GREY_MID)
    tag = 'HYUNDAI COE MOBILITY INNOVATION IDEATHON 2026'
    pdf.c.drawCentredString(W/2, H - 60, tag)

    # Track pill
    pdf.c.setFillColor(GREY3)
    pdf.c.roundRect(W/2 - 95, H - 84, 190, 18, 9, fill=1, stroke=0)
    pdf.c.setFillColor(YELLOW)
    pdf.c.setFont('Helvetica-Bold', 8)
    pdf.c.drawCentredString(W/2, H - 78, 'TRACK B  ·  TECHNOLOGY & PROTOTYPE')

    # Title
    pdf.c.setFont('Helvetica-Bold', 52)
    pdf.c.setFillColor(WHITE)
    pdf.c.drawCentredString(W/2, H - 160, 'Hyundai')
    pdf.c.setFillColor(YELLOW)
    pdf.c.drawCentredString(W/2, H - 218, 'SmartReserve')

    # Yellow underline
    pdf.c.setFillColor(YELLOW)
    pdf.c.rect(W/2 - 80, H - 230, 160, 3, fill=1, stroke=0)

    # Tagline
    pdf.c.setFont('Helvetica', 12)
    pdf.c.setFillColor(OFFWHITE)
    lines = [
        'An AI-Powered In-Cabin EV Charging Co-Pilot that',
        'finds free chargers in your district, predicts congestion,',
        'and physically locks a 30-minute exclusive slot',
        'all from the car screen.'
    ]
    for i, line in enumerate(lines):
        pdf.c.drawCentredString(W/2, H - 268 - i*16, line)

    # Pills row
    py = H - 340
    pills = ['Category 2: AI-Powered & Digital In-Vehicle Experience', 'Rs.10 Lakh Prize Pool', 'OCPP 1.6 Protocol']
    total_w = sum(pdf.c.stringWidth(p, 'Helvetica-Bold', 8) + 20 for p in pills) + 16
    px = W/2 - total_w/2
    for p in pills:
        tw = pdf.c.stringWidth(p, 'Helvetica-Bold', 8)
        pw = tw + 20
        pdf.c.setFillColor(GREY3)
        pdf.c.roundRect(px, py - 3, pw, 16, 8, fill=1, stroke=0)
        pdf.c.setFillColor(YELLOW)
        pdf.c.setFont('Helvetica-Bold', 8)
        pdf.c.drawString(px + 10, py + 1, p)
        px += pw + 8

    # Stats bar
    stats = [('3','SOFTWARE\nSCREENS'), ('934','REAL STATION\nNODES'), ('78%','GNN\nACCURACY'), ('30s','DEMO\nTIME')]
    sw = (W - 120) / 4
    sy = H - 430
    for i, (val, lab) in enumerate(stats):
        sx = 60 + i * sw
        pdf.c.setFillColor(GREY2)
        pdf.c.roundRect(sx + 4, sy, sw - 8, 72, 6, fill=1, stroke=0)
        pdf.c.setFillColor(YELLOW)
        pdf.c.rect(sx + 4, sy + 70, sw - 8, 2, fill=1, stroke=0)
        pdf.c.setFont('Helvetica-Bold', 30)
        pdf.c.setFillColor(YELLOW)
        pdf.c.drawCentredString(sx + sw/2, sy + 38, val)
        pdf.c.setFont('Helvetica', 7)
        pdf.c.setFillColor(GREY_MID)
        for j, part in enumerate(lab.split('\n')):
            pdf.c.drawCentredString(sx + sw/2, sy + 20 - j*10, part)

    # Bottom credits
    pdf.c.setFont('Helvetica', 8)
    pdf.c.setFillColor(GREY_MID)
    pdf.c.drawCentredString(W/2, 40, 'Built for Hyundai CoE Mobility Innovation Ideathon 2026  ·  Real BEE + Parivahan Data')

    # ── PAGE 2: THE PROBLEM ───────────────────────────────────────────────────
    pdf.new_page()
    y = pdf.section_header('SECTION 01 — THE PROBLEM', 'Why Indian EV Drivers Are Suffering', H - 50)

    pdf.c.setFont('Helvetica', 10)
    pdf.c.setFillColor(OFFWHITE)
    pdf.c.drawString(30, y, 'India has over 60,000 registered EVs in Telangana alone — but the charging experience is completely broken.')
    y -= 20

    # 4 problem cards
    problems = [
        ('P1', 'NO LIVE STATUS', 'Apps like ChargeZone and Statiq only show whether chargers exist — not whether a specific connector port is free right now. The "Available" badge is often 24 hours old.'),
        ('P2', 'PHONE WHILE DRIVING', 'Drivers must pull out their phone while driving, open 3 different apps, and manually search — which is both dangerous and illegal in India.'),
        ('P3', 'NO RESERVATION', 'Even if you know a charger is free, someone else can arrive before you and steal your spot. There is no way to lock a connector in advance.'),
        ('P4', 'ICE VEHICLE BLOCKING', 'Non-electric cars park at charging stations (called ICE-ing). There is no enforcement or economic penalty to stop this from happening.'),
    ]
    card_w = (W - 70) / 2
    card_h = 100
    for i, (num, title, desc) in enumerate(problems):
        cx_ = 30 + (i % 2) * (card_w + 10)
        cy_ = y - (i // 2) * (card_h + 12) - card_h
        # Card bg
        pdf.c.setFillColor(GREY2)
        pdf.c.roundRect(cx_, cy_, card_w, card_h, 5, fill=1, stroke=0)
        # Left border
        pdf.c.setFillColor(YELLOW)
        pdf.c.rect(cx_, cy_, 3, card_h, fill=1, stroke=0)
        # Number badge
        pdf.c.setFillColor(YELLOW)
        pdf.c.circle(cx_ + 18, cy_ + card_h - 16, 10, fill=1, stroke=0)
        pdf.c.setFont('Helvetica-Bold', 8)
        pdf.c.setFillColor(BLACK)
        pdf.c.drawCentredString(cx_ + 18, cy_ + card_h - 19, num)
        # Title
        pdf.c.setFont('Helvetica-Bold', 11)
        pdf.c.setFillColor(WHITE)
        pdf.c.drawString(cx_ + 36, cy_ + card_h - 20, title)
        # Description
        pdf.c.setFont('Helvetica', 8.5)
        pdf.c.setFillColor(OFFWHITE)
        wrapped = textwrap.wrap(desc, 50)
        for j, line in enumerate(wrapped[:4]):
            pdf.c.drawString(cx_ + 12, cy_ + card_h - 44 - j * 12, line)

    y -= (card_h * 2 + 12 + 20)

    # Current journey flow
    pdf.c.setFont('Helvetica-Bold', 9)
    pdf.c.setFillColor(YELLOW)
    pdf.c.drawString(30, y, 'CURRENT PAINFUL JOURNEY (WITHOUT SmartReserve)')
    y -= 12
    pdf.c.setFillColor(GREY2)
    pdf.c.roundRect(30, y - 50, W - 60, 52, 5, fill=1, stroke=0)

    steps = [('18%','Battery\nCritical'), ('3','Open 3\nApps'), ('?','No Live\nStatus'), ('20m','Drive to\nStation'), ('X','Charger\nOccupied'), ('60m','Wait in\nQueue'), ('!','40 Min\nWasted')]
    sw2 = (W - 60) / len(steps)
    for i, (icon, label) in enumerate(steps):
        sx = 30 + i * sw2 + sw2/2
        sy2 = y - 24
        pdf.c.setFillColor(GREY3)
        pdf.c.circle(sx, sy2, 12, fill=1, stroke=0)
        pdf.c.setFont('Helvetica-Bold', 8)
        pdf.c.setFillColor(YELLOW if i not in [2,4] else colors.HexColor('#EF4444'))
        pdf.c.drawCentredString(sx, sy2 - 3, icon)
        pdf.c.setFont('Helvetica', 6.5)
        pdf.c.setFillColor(GREY_MID)
        for j, part in enumerate(label.split('\n')):
            pdf.c.drawCentredString(sx, sy2 - 24 - j * 8, part)
        if i < len(steps) - 1:
            pdf.c.setStrokeColor(GREY3)
            pdf.c.setLineWidth(0.8)
            pdf.c.line(sx + 14, sy2, sx + sw2 - 14, sy2)

    # ── PAGE 3: THE SOLUTION ──────────────────────────────────────────────────
    pdf.new_page()
    y = pdf.section_header('SECTION 02 — THE SOLUTION', 'Hyundai SmartReserve: What We Built', H - 50)

    # Big idea box
    pdf.c.setFillColor(colors.HexColor('#1A1500'))
    pdf.c.roundRect(30, y - 60, W - 60, 62, 6, fill=1, stroke=0)
    pdf.c.setFillColor(YELLOW)
    pdf.c.rect(30, y, W - 60, 2, fill=1, stroke=0)
    pdf.c.setFont('Helvetica-Bold', 8)
    pdf.c.setFillColor(YELLOW)
    pdf.c.drawString(42, y - 14, 'THE BIG IDEA — ONE SENTENCE')
    pdf.c.setFont('Helvetica', 10)
    pdf.c.setFillColor(OFFWHITE)
    big_idea = 'A software system that shows all charging station live statuses on the Hyundai in-car touchscreen, predicts waiting time using AI, and lets the driver press one button to physically lock a charger for 30 minutes — only unlockable with a secret PIN or QR code.'
    wrapped = textwrap.wrap(big_idea, 88)
    for j, line in enumerate(wrapped):
        pdf.c.drawString(42, y - 30 - j * 14, line)
    y -= 76

    # 3 pillars
    y -= 8
    pillars = [
        ('1', 'In-Cabin Dashboard', 'The Hyundai car touchscreen view. Shows live map, AI predictions, and the Reserve button. Driver never touches their phone.'),
        ('2', 'Cloud AI Brain', 'FastAPI backend + Stanford ROLAND GNN model. Predicts congestion, manages reservations, and sends lock commands via OCPP 1.6.'),
        ('3', 'Charger Kiosk Simulator', 'Simulated charger touchscreen that physically locks out unauthorized users until the correct PIN is entered.'),
    ]
    pw2 = (W - 70) / 3
    for i, (num, title, desc) in enumerate(pillars):
        px = 30 + i * (pw2 + 5)
        py = y - 110
        pdf.c.setFillColor(GREY2)
        pdf.c.roundRect(px, py, pw2, 110, 5, fill=1, stroke=0)
        # Top yellow band
        pdf.c.setFillColor(YELLOW)
        pdf.c.rect(px, py + 108, pw2, 2, fill=1, stroke=0)
        # Number
        pdf.c.setFillColor(YELLOW)
        pdf.c.roundRect(px + 10, py + 80, 24, 20, 3, fill=1, stroke=0)
        pdf.c.setFont('Helvetica-Bold', 12)
        pdf.c.setFillColor(BLACK)
        pdf.c.drawCentredString(px + 22, py + 84, num)
        # Title
        pdf.c.setFont('Helvetica-Bold', 10)
        pdf.c.setFillColor(WHITE)
        wrapped_t = textwrap.wrap(title, 22)
        for j, line in enumerate(wrapped_t):
            pdf.c.drawString(px + 10, py + 72 - j * 13, line)
        # Desc
        pdf.c.setFont('Helvetica', 8)
        pdf.c.setFillColor(GREY_MID)
        wrapped_d = textwrap.wrap(desc, 28)
        for j, line in enumerate(wrapped_d[:5]):
            pdf.c.drawString(px + 10, py + 44 - j * 10, line)

    y -= 128

    # New journey flow
    pdf.c.setFont('Helvetica-Bold', 9)
    pdf.c.setFillColor(YELLOW)
    pdf.c.drawString(30, y, 'NEW SMOOTH JOURNEY (WITH SmartReserve)')
    y -= 12
    pdf.c.setFillColor(GREY2)
    pdf.c.roundRect(30, y - 55, W - 60, 57, 5, fill=1, stroke=0)

    new_steps = [('18%','Battery\nDetected'), ('AI','Best Charger\nFound'), ('1','One Tap\nReserve'), ('LOCK','Charger\nLocked'), ('PIN','PIN\nReceived'), ('18m','Drive\nThere'), ('KEY','Type PIN'), ('60kW','Charging\nStarts!')]
    sw3 = (W - 60) / len(new_steps)
    for i, (icon, label) in enumerate(new_steps):
        sx = 30 + i * sw3 + sw3/2
        sy2 = y - 26
        pdf.c.setFillColor(YELLOW)
        pdf.c.circle(sx, sy2, 11, fill=1, stroke=0)
        pdf.c.setFont('Helvetica-Bold', 7)
        pdf.c.setFillColor(BLACK)
        pdf.c.drawCentredString(sx, sy2 - 3, icon)
        pdf.c.setFont('Helvetica', 6)
        pdf.c.setFillColor(OFFWHITE)
        for j, part in enumerate(label.split('\n')):
            pdf.c.drawCentredString(sx, sy2 - 22 - j * 8, part)
        if i < len(new_steps) - 1:
            pdf.c.setStrokeColor(YELLOW)
            pdf.c.setLineWidth(0.8)
            pdf.c.line(sx + 13, sy2, sx + sw3 - 13, sy2)

    y -= 72

    # Category fit box
    pdf.c.setFillColor(colors.HexColor('#1A1500'))
    pdf.c.roundRect(30, y - 50, W - 60, 52, 5, fill=1, stroke=0)
    pdf.c.setFillColor(YELLOW)
    pdf.c.setFont('Helvetica-Bold', 9)
    pdf.c.drawString(42, y - 14, 'WHY THIS PERFECTLY FITS IDEATHON CATEGORY 2')
    pdf.c.setFont('Helvetica', 9)
    pdf.c.setFillColor(OFFWHITE)
    fit_text = 'Category 2 asks: "How can AI, digital content, and software create new experiences inside the vehicle?" Our answer: AI predicts which charger will be free. The car screen shows this to the driver. The driver never leaves the car experience — no phone, no confusion, no anxiety. Pure in-vehicle intelligence.'
    wrapped = textwrap.wrap(fit_text, 90)
    for j, line in enumerate(wrapped[:3]):
        pdf.c.drawString(42, y - 30 - j * 13, line)

    # ── PAGE 4: ARCHITECTURE ──────────────────────────────────────────────────
    pdf.new_page()
    y = pdf.section_header('SECTION 03 — FULL ARCHITECTURE', 'How Everything Connects', H - 50)

    # Architecture diagram (text-based)
    pdf.c.setFont('Helvetica-Bold', 9)
    pdf.c.setFillColor(YELLOW)
    pdf.c.drawString(30, y, 'END-TO-END REAL-TIME PIPELINE: 100% PURE SOFTWARE — ZERO HARDWARE')
    y -= 16

    # 3 component boxes
    comp_w = (W - 70) / 3
    comp_h = 160
    components = [
        ('1', 'IN-CABIN DASHBOARD', 'HYUNDAI AVNT / CCNC', [
            ('Battery Telematics', '18% / 41km'),
            ('District Station Map', 'Live Pins'),
            ('AI Congestion Badge', 'GNN Queue Time'),
            ('1-Touch 30-Min Lock', 'Reserve Slot'),
        ]),
        ('2', 'CLOUD AI BRAIN', 'PYTHON FASTAPI + WEBSOCKET', [
            ('Stanford ROLAND GNN', 'R2=80.02%'),
            ('OCPP 1.6 Protocol Broker', 'Official Spec'),
            ('WebSocket Hub', '<100ms Sync'),
            ('Telangana Station DB', '934 CSV'),
        ]),
        ('3', 'CHARGER KIOSK SIM', 'DIGITAL TWIN LOCKOUT SCREEN', [
            ('Default State', 'AVAILABLE'),
            ('Hardware Lockout State', 'RESERVED'),
            ('PIN Keypad / QR Sensor', 'PIN: 482910'),
            ('Charging Output', '60kW DC Flow'),
        ]),
    ]
    for i, (num, title, sub, items) in enumerate(components):
        cx = 30 + i * (comp_w + 5)
        cy = y - comp_h
        pdf.c.setFillColor(GREY2)
        pdf.c.roundRect(cx, cy, comp_w, comp_h, 5, fill=1, stroke=0)
        pdf.c.setFillColor(YELLOW)
        pdf.c.rect(cx, cy + comp_h - 2, comp_w, 2, fill=1, stroke=0)
        # Header
        pdf.c.setFillColor(YELLOW)
        pdf.c.roundRect(cx + 8, cy + comp_h - 24, 20, 16, 3, fill=1, stroke=0)
        pdf.c.setFont('Helvetica-Bold', 10)
        pdf.c.setFillColor(BLACK)
        pdf.c.drawCentredString(cx + 18, cy + comp_h - 19, num)
        pdf.c.setFont('Helvetica-Bold', 9)
        pdf.c.setFillColor(WHITE)
        pdf.c.drawString(cx + 34, cy + comp_h - 17, title)
        pdf.c.setFont('Helvetica', 7)
        pdf.c.setFillColor(GREY_MID)
        pdf.c.drawString(cx + 34, cy + comp_h - 28, sub)

        # Items
        for j, (label, value) in enumerate(items):
            iy = cy + comp_h - 50 - j * 26
            pdf.c.setFillColor(GREY1)
            pdf.c.roundRect(cx + 8, iy, comp_w - 16, 20, 2, fill=1, stroke=0)
            pdf.c.setFont('Helvetica', 8)
            pdf.c.setFillColor(OFFWHITE)
            pdf.c.drawString(cx + 14, iy + 6, label)
            pdf.c.setFont('Helvetica-Bold', 7)
            pdf.c.setFillColor(YELLOW)
            pdf.c.drawRightString(cx + comp_w - 14, iy + 6, value)

    y -= comp_h + 20

    # Data flow table
    pdf.c.setFont('Helvetica-Bold', 9)
    pdf.c.setFillColor(YELLOW)
    pdf.c.drawString(30, y, 'WHAT DATA FLOWS BETWEEN THE PARTS')
    y -= 14

    flows = [
        ('Car Dashboard', 'Cloud API',      'Reserve request: Station ID, Duration 30 min, User ID',    'HTTP POST'),
        ('Cloud API',     'Charger Kiosk',  'Lock command: ReserveNow with OTP token',                  'WebSocket'),
        ('Cloud API',     'Car Dashboard',  'Confirmation + 6-digit PIN + countdown timer',             'HTTP Response'),
        ('Charger Kiosk', 'Cloud API',      'PIN entered by driver for verification',                   'HTTP POST'),
        ('Cloud API',     'Charger Kiosk',  'Unlock signal: close relay, start charging session',       'WebSocket'),
        ('Charger Kiosk', 'Car Dashboard',  'Live meter: kW, kWh, SoC% (simulated battery graph)',     'WebSocket'),
    ]
    col_ws = [90, 90, 250, 75]
    total_cw = sum(col_ws)
    scale = (W - 60) / total_cw
    col_ws = [int(c * scale) for c in col_ws]
    headers2 = ['FROM', 'TO', 'WHAT IS SENT', 'PROTOCOL']
    row_h2 = 22

    # Header
    pdf.c.setFillColor(YELLOW)
    pdf.c.rect(30, y - row_h2, W - 60, row_h2, fill=1, stroke=0)
    x = 30
    for h, cw in zip(headers2, col_ws):
        pdf.c.setFont('Helvetica-Bold', 8)
        pdf.c.setFillColor(BLACK)
        pdf.c.drawString(x + 6, y - row_h2 + 7, h)
        x += cw
    y -= row_h2

    proto_colors2 = {'HTTP POST': YELLOW, 'WebSocket': YELLOW2, 'HTTP Response': YELLOW_DIM}
    for ri, row in enumerate(flows):
        bg = GREY1 if ri % 2 == 0 else GREY2
        pdf.c.setFillColor(bg)
        pdf.c.rect(30, y - row_h2, W - 60, row_h2, fill=1, stroke=0)
        x = 30
        for ci, (cell, cw) in enumerate(zip(row, col_ws)):
            if ci == 3:
                pc = proto_colors2.get(cell, YELLOW)
                pdf.c.setFillColor(pc)
                pdf.c.roundRect(x + 4, y - row_h2 + 4, cw - 8, 14, 3, fill=1, stroke=0)
                pdf.c.setFont('Helvetica-Bold', 7)
                pdf.c.setFillColor(BLACK)
                pdf.c.drawCentredString(x + cw/2, y - row_h2 + 8, cell)
            else:
                pdf.c.setFont('Helvetica', 8)
                pdf.c.setFillColor(OFFWHITE)
                pdf.c.drawString(x + 6, y - row_h2 + 7, str(cell)[:40])
            x += cw
        pdf.c.setStrokeColor(GREY3)
        pdf.c.setLineWidth(0.3)
        pdf.c.line(30, y - row_h2, W - 30, y - row_h2)
        y -= row_h2

    # ── PAGE 5: CHARGER LOCK ──────────────────────────────────────────────────
    pdf.new_page()
    y = pdf.section_header('SECTION 04 — THE CHARGER LOCK SYSTEM', 'How the 30-Minute Lock Works', H - 50)

    # State machine diagram
    pdf.c.setFont('Helvetica-Bold', 8)
    pdf.c.setFillColor(YELLOW)
    pdf.c.drawString(30, y, 'CHARGER STATE MACHINE — ALL POSSIBLE STATES')
    y -= 10

    sm_h = 200
    pdf.c.setFillColor(GREY2)
    pdf.c.roundRect(30, y - sm_h, W - 60, sm_h, 6, fill=1, stroke=0)

    def sm_box(label, bx, by, bw=100, bh=24, bg=YELLOW, tc=BLACK):
        pdf.c.setFillColor(bg)
        pdf.c.roundRect(bx - bw/2, by - bh/2, bw, bh, 4, fill=1, stroke=0)
        pdf.c.setFont('Helvetica-Bold', 8)
        pdf.c.setFillColor(tc)
        pdf.c.drawCentredString(bx, by - 3, label)

    def sm_arrow(x1, y1, x2, y2, label=''):
        pdf.c.setStrokeColor(GREY_MID)
        pdf.c.setLineWidth(0.8)
        pdf.c.line(x1, y1, x2, y2)
        if label:
            mx, my = (x1+x2)/2, (y1+y2)/2
            pdf.c.setFont('Helvetica', 6.5)
            pdf.c.setFillColor(GREY_MID)
            pdf.c.drawCentredString(mx, my + 3, label)

    scx = W/2
    sbase = y - sm_h + 16

    sm_box('System Starts', scx, sbase + sm_h - 24, bw=100, bh=18, bg=GREY3, tc=WHITE)
    sm_arrow(scx, sbase + sm_h - 33, scx, sbase + sm_h - 56)
    sm_box('AVAILABLE', scx, sbase + sm_h - 66, bw=110, bh=24, bg=YELLOW, tc=BLACK)
    sm_arrow(scx, sbase + sm_h - 78, scx, sbase + sm_h - 104, 'ReserveNow')
    sm_box('RESERVED', scx, sbase + sm_h - 114, bw=110, bh=24, bg=colors.HexColor('#3D3000'), tc=YELLOW)
    sm_arrow(scx - 55, sbase + sm_h - 114, scx - 120, sbase + sm_h - 140, 'Wrong PIN')
    sm_box('ACCESS DENIED', scx - 135, sbase + sm_h - 150, bw=90, bh=20, bg=GREY3, tc=WHITE)
    sm_arrow(scx - 110, sbase + sm_h - 150, scx - 55, sbase + sm_h - 124, 'Retry (3x)')
    sm_arrow(scx + 55, sbase + sm_h - 114, scx + 120, sbase + sm_h - 140, 'Correct PIN')
    sm_box('UNLOCKED', scx + 135, sbase + sm_h - 150, bw=90, bh=20, bg=GREY2, tc=YELLOW)
    sm_arrow(scx + 105, sbase + sm_h - 155, scx + 70, sbase + sm_h - 175, 'Relay closes')
    sm_box('CHARGING', scx, sbase + sm_h - 182, bw=110, bh=24, bg=YELLOW, tc=BLACK)
    sm_arrow(scx + 55, sbase + sm_h - 182, scx + 135, sbase + sm_h - 182)
    sm_box('FINISHING', scx + 185, sbase + sm_h - 182, bw=90, bh=20, bg=GREY2, tc=YELLOW2)
    # Timer expire arrow
    pdf.c.setStrokeColor(GREY_MID)
    pdf.c.setLineWidth(0.8)
    pdf.c.line(scx - 55, sbase + sm_h - 124, scx - 165, sbase + sm_h - 124)
    pdf.c.line(scx - 165, sbase + sm_h - 124, scx - 165, sbase + sm_h - 66)
    pdf.c.line(scx - 165, sbase + sm_h - 66, scx - 55, sbase + sm_h - 66)
    pdf.c.setFont('Helvetica', 6)
    pdf.c.setFillColor(GREY_MID)
    pdf.c.drawString(scx - 210, sbase + sm_h - 100, '30min expire')

    y -= sm_h + 16

    # State table
    pdf.c.setFont('Helvetica-Bold', 9)
    pdf.c.setFillColor(YELLOW)
    pdf.c.drawString(30, y, 'WHAT THE CHARGER SCREEN SHOWS IN EACH STATE')
    y -= 12

    states = [
        ('AVAILABLE',    'Green Solid',   '60 kW CCS2 Available — Plug in or tap RFID',          'Relay Open · Ready'),
        ('RESERVED',     'Blue Solid',    'RESERVED for Hyundai Driver — Enter 6-digit PIN',      'Relay LOCKED · 0 kW'),
        ('ACCESS DENIED','Red Flash',     'WRONG PIN — Access Denied. Connector stays locked',    'Relay LOCKED · 0 kW'),
        ('UNLOCKED',     'Green Blink',   'Identity Verified! Plug in your cable now',            'Awaiting plug-in'),
        ('CHARGING',     'Green Solid',   'Charging: 60 kW · SoC: 42% · 28 min remaining',      'Relay CLOSED · 60 kW'),
        ('FINISHING',    'Amber Solid',   'Charging complete at 80%. Please unplug now.',         'Idle fee starts 5 min'),
    ]
    trow_h = 24
    col_ws2 = [90, 75, 240, 100]
    scale2 = (W - 60) / sum(col_ws2)
    col_ws2 = [int(c * scale2) for c in col_ws2]
    hdrs2 = ['STATE', 'LED COLOR', 'SCREEN MESSAGE', 'POWER STATUS']

    pdf.c.setFillColor(YELLOW)
    pdf.c.rect(30, y - trow_h, W - 60, trow_h, fill=1, stroke=0)
    x = 30
    for h, cw in zip(hdrs2, col_ws2):
        pdf.c.setFont('Helvetica-Bold', 8)
        pdf.c.setFillColor(BLACK)
        pdf.c.drawString(x + 6, y - trow_h + 8, h)
        x += cw
    y -= trow_h

    for ri, row in enumerate(states):
        bg = GREY1 if ri % 2 == 0 else GREY2
        pdf.c.setFillColor(bg)
        pdf.c.rect(30, y - trow_h, W - 60, trow_h, fill=1, stroke=0)
        x = 30
        for ci, (cell, cw) in enumerate(zip(row, col_ws2)):
            if ci == 0:
                pdf.c.setFont('Helvetica-Bold', 8)
                pdf.c.setFillColor(YELLOW)
            elif ci == 1:
                pdf.c.setFont('Helvetica', 8)
                pdf.c.setFillColor(YELLOW2)
            else:
                pdf.c.setFont('Helvetica', 8)
                pdf.c.setFillColor(OFFWHITE)
            pdf.c.drawString(x + 6, y - trow_h + 8, str(cell)[:45])
            x += cw
        pdf.c.setStrokeColor(GREY3)
        pdf.c.setLineWidth(0.3)
        pdf.c.line(30, y - trow_h, W - 30, y - trow_h)
        y -= trow_h

    y -= 12
    # Physical lock explanation
    pdf.c.setFillColor(colors.HexColor('#1A1500'))
    pdf.c.roundRect(30, y - 52, W - 60, 54, 5, fill=1, stroke=0)
    pdf.c.setFont('Helvetica-Bold', 8)
    pdf.c.setFillColor(YELLOW)
    pdf.c.drawString(42, y - 14, 'HOW PHYSICAL LOCK WORKS (NO HARDWARE NEEDED TO DEMO)')
    pdf.c.setFont('Helvetica', 9)
    pdf.c.setFillColor(OFFWHITE)
    lock_text = 'In the real world, the charger\'s internal microcontroller runs the OCPP state machine. The contactor relay opens and closes based on OCPP commands. In our software simulator, this is simply a JavaScript boolean variable: relayOpen = true/false. When false, the screen shows "0 kW" and "Access Denied" — perfectly mimicking real hardware behavior for judges.'
    wrapped = textwrap.wrap(lock_text, 90)
    for j, line in enumerate(wrapped[:3]):
        pdf.c.drawString(42, y - 30 - j * 13, line)

    # ── PAGE 6: USER JOURNEY ──────────────────────────────────────────────────
    pdf.new_page()
    y = pdf.section_header('SECTION 05 — COMPLETE USER JOURNEY', 'Step-by-Step Demo Flow', H - 50)

    steps_flow = [
        ('01', 'Driver in Car — Battery at 18%', 'System detects low SoC. Automatically queries AI for nearest free 60kW charger. Displays map on car screen.'),
        ('02', 'GET /stations — Load 934 stations', 'FastAPI fetches all Telangana stations. GNN model scores each for congestion. Map shows color-coded pins.'),
        ('03', 'Driver taps Reserve for 30 Minutes', 'One tap on car screen. POST /reserve sends station_id, duration:30, user_id to cloud. No phone needed.'),
        ('04', 'Cloud generates PIN + sends ReserveNow', 'Backend creates 6-digit PIN (e.g. 482910). OCPP ReserveNow command pushed via WebSocket to kiosk.'),
        ('05', 'Kiosk screen turns BLUE — LOCKED', 'Charger display changes from Available to Reserved. Relay stays OPEN. Any plug-in attempt is blocked.'),
        ('06', 'Car screen shows PIN + countdown', 'Dashboard displays: PIN: 482910 | Slot expires in 29:58. Driver drives 1.8km to Gachibowli.'),
        ('07', 'Unauthorized person types 111111', 'Kiosk flashes RED: Access Denied. Contactor remains locked. 0 kW. Proves physical lockout is real.'),
        ('08', 'Driver types correct PIN 482910', 'Kiosk verifies via POST /verify-otp. WebSocket UNLOCK_RELAY sent. Relay CLOSES. 60kW DC charging begins.'),
    ]
    step_h = 52
    for i, (num, title, desc) in enumerate(steps_flow):
        sx = 30
        sy = y - i * (step_h + 6) - step_h
        if sy < 50:
            break
        pdf.c.setFillColor(GREY2)
        pdf.c.roundRect(sx, sy, W - 60, step_h, 4, fill=1, stroke=0)
        # Number circle
        pdf.c.setFillColor(YELLOW)
        pdf.c.circle(sx + 24, sy + step_h/2, 14, fill=1, stroke=0)
        pdf.c.setFont('Helvetica-Bold', 10)
        pdf.c.setFillColor(BLACK)
        pdf.c.drawCentredString(sx + 24, sy + step_h/2 - 4, num)
        # Title
        pdf.c.setFont('Helvetica-Bold', 10)
        pdf.c.setFillColor(WHITE)
        pdf.c.drawString(sx + 48, sy + step_h - 16, title)
        # Description
        pdf.c.setFont('Helvetica', 9)
        pdf.c.setFillColor(GREY_MID)
        wrapped = textwrap.wrap(desc, 78)
        for j, line in enumerate(wrapped[:2]):
            pdf.c.drawString(sx + 48, sy + step_h - 30 - j * 12, line)

    # ── PAGE 7: AI BRAIN ──────────────────────────────────────────────────────
    pdf.new_page()
    y = pdf.section_header('SECTION 06 — THE AI BRAIN', 'How the GNN Predicts Congestion', H - 50)

    pdf.c.setFont('Helvetica', 10)
    pdf.c.setFillColor(OFFWHITE)
    intro = 'Most apps just show you whether a charger is currently free. Our AI goes further — it predicts which stations will be congested in the next few hours across the entire district, so you always pick the smartest charger, not just the nearest one.'
    wrapped = textwrap.wrap(intro, 90)
    for line in wrapped:
        pdf.c.drawString(30, y, line)
        y -= 14
    y -= 6

    # GNN architecture diagram
    gnn_h = 180
    pdf.c.setFillColor(GREY2)
    pdf.c.roundRect(30, y - gnn_h, W - 60, gnn_h, 6, fill=1, stroke=0)
    pdf.c.setFillColor(YELLOW)
    pdf.c.rect(30, y, W - 60, 2, fill=1, stroke=0)
    pdf.c.setFont('Helvetica-Bold', 8)
    pdf.c.setFillColor(YELLOW)
    pdf.c.drawString(42, y - 14, 'STANFORD ROLAND GRAPH NEURAL NETWORK — ARCHITECTURE')

    # Input column
    col_x = 60
    pdf.c.setFont('Helvetica-Bold', 8)
    pdf.c.setFillColor(YELLOW)
    pdf.c.drawString(col_x, y - 28, 'INPUT — 52 FEATURES')
    inputs = ['Static: Power Rating, Connector,\n  GPS Location', 'Dynamic: kWh/kW Load\n  Lag 1, 2, 3 months', 'Seasonal: Month sin/cos,\n  Quarter of year', 'POI: Nearby shops, malls,\n  Restaurants, hospitals']
    for i, inp in enumerate(inputs):
        iy = y - 46 - i * 32
        pdf.c.setFillColor(GREY1)
        pdf.c.roundRect(col_x, iy, 130, 28, 3, fill=1, stroke=0)
        pdf.c.setFont('Helvetica', 7.5)
        pdf.c.setFillColor(OFFWHITE)
        for j, line in enumerate(inp.split('\n')):
            pdf.c.drawString(col_x + 6, iy + 18 - j*10, line)

    # Model column
    mid_x = W/2 - 60
    pdf.c.setFont('Helvetica-Bold', 8)
    pdf.c.setFillColor(YELLOW)
    pdf.c.drawString(mid_x, y - 28, 'GNN MODEL LAYERS')
    model_layers = ['ResidualEdgeConv Layer\n  Spatial neighbor aggregation', 'GRU Updater\n  Temporal memory (monthly)', 'Regression Head\n  Predicts next-month values']
    for i, layer in enumerate(model_layers):
        iy = y - 46 - i * 44
        pdf.c.setFillColor(colors.HexColor('#1A1500'))
        pdf.c.roundRect(mid_x, iy, 130, 38, 3, fill=1, stroke=0)
        pdf.c.setFillColor(YELLOW)
        pdf.c.rect(mid_x, iy + 36, 130, 2, fill=1, stroke=0)
        pdf.c.setFont('Helvetica', 7.5)
        pdf.c.setFillColor(OFFWHITE)
        for j, line in enumerate(layer.split('\n')):
            pdf.c.drawString(mid_x + 6, iy + 26 - j*12, line)

    # Output column
    out_x = W - 190
    pdf.c.setFont('Helvetica-Bold', 8)
    pdf.c.setFillColor(YELLOW)
    pdf.c.drawString(out_x, y - 28, 'OUTPUT PER STATION')
    outputs = ['Monthly Units (kWh)\n  Energy consumed forecast', 'Peak Load (kW)\n  Max power demand', 'Congestion Score\n  LOW / MEDIUM / HIGH']
    for i, out in enumerate(outputs):
        iy = y - 46 - i * 44
        pdf.c.setFillColor(GREY1)
        pdf.c.roundRect(out_x, iy, 130, 38, 3, fill=1, stroke=0)
        pdf.c.setFillColor(YELLOW)
        pdf.c.circle(out_x + 8, iy + 28, 5, fill=1, stroke=0)
        pdf.c.setFont('Helvetica', 7.5)
        pdf.c.setFillColor(OFFWHITE)
        for j, line in enumerate(out.split('\n')):
            pdf.c.drawString(out_x + 18, iy + 28 - j*12, line)

    # Arrows between columns
    for i in range(4):
        ay = y - 46 - i * 32 + 14
        if ay > y - gnn_h + 10:
            pdf.c.setStrokeColor(GREY3)
            pdf.c.setLineWidth(0.8)
            pdf.c.line(col_x + 130, ay, mid_x, ay)
    for i in range(3):
        ay = y - 46 - i * 44 + 19
        pdf.c.setStrokeColor(GREY3)
        pdf.c.setLineWidth(0.8)
        pdf.c.line(mid_x + 130, ay, out_x, ay)

    y -= gnn_h + 16

    # Stats cards
    stat_cards = [
        ('TRAINED MODEL', 'GNN trained on 36 months of real data (Jan 2023 – Dec 2025)\nacross 934 charging stations in Telangana.\nPre-trained weights: roland_final_weights_80_02.pt'),
        ('REAL DATASET', 'Data from two official Indian government sources:\nBureau of Energy Efficiency (BEE)\nParivahan Sewa (Ministry of Road Transport). Not dummy data.'),
        ('MODEL PERFORMANCE', 'R2 Score = 78.50% to 80.02% on test set.\nCorrectly explains 80% of demand variation\nacross all 33 districts of Telangana.'),
        ('COVERAGE', 'All 33 districts of Telangana:\nHyderabad, Rangareddy, Medchal-Malkajgiri,\nSangareddy — all major Hyundai EV markets.'),
    ]
    sc_w = (W - 70) / 2
    sc_h = 72
    for i, (title, desc) in enumerate(stat_cards):
        scx2 = 30 + (i % 2) * (sc_w + 10)
        scy = y - (i // 2) * (sc_h + 10) - sc_h
        pdf.c.setFillColor(GREY2)
        pdf.c.roundRect(scx2, scy, sc_w, sc_h, 4, fill=1, stroke=0)
        pdf.c.setFillColor(YELLOW)
        pdf.c.rect(scx2, scy + sc_h - 2, sc_w, 2, fill=1, stroke=0)
        pdf.c.setFont('Helvetica-Bold', 8)
        pdf.c.setFillColor(YELLOW)
        pdf.c.drawString(scx2 + 10, scy + sc_h - 16, title)
        pdf.c.setFont('Helvetica', 8)
        pdf.c.setFillColor(OFFWHITE)
        for j, line in enumerate(desc.split('\n')):
            pdf.c.drawString(scx2 + 10, scy + sc_h - 30 - j * 12, line)

    # ── PAGE 8: TECH STACK ────────────────────────────────────────────────────
    pdf.new_page()
    y = pdf.section_header('SECTION 07 & 08 — BUILD PLAN & TECH STACK', 'Technology Stack and File Structure', H - 50)

    # File structure
    pdf.c.setFont('Helvetica-Bold', 9)
    pdf.c.setFillColor(YELLOW)
    pdf.c.drawString(30, y, 'PROJECT FILE STRUCTURE')
    y -= 12

    pdf.c.setFillColor(GREY1)
    pdf.c.roundRect(30, y - 160, W - 60, 162, 5, fill=1, stroke=0)
    pdf.c.setFont('Courier', 8.5)
    pdf.c.setFillColor(YELLOW)
    tree_lines = [
        ('EV_Hyundai/',                              YELLOW),
        ('  EVCS_Demand_Forecasting/     (DONE)',     YELLOW2),
        ('    final_model/',                          GREY_MID),
        ('      roland_final_weights_80_02.pt',       OFFWHITE),
        ('    processed/nodes_master.csv',            OFFWHITE),
        ('  smartreserve_backend/',                   YELLOW),
        ('    main.py             FastAPI server, all endpoints', OFFWHITE),
        ('    websocket_manager.py  Real-time connection hub', OFFWHITE),
        ('    ocpp_engine.py       OCPP 1.6 protocol commands', OFFWHITE),
        ('    gnn_predictor.py     Loads GNN + predictions', OFFWHITE),
        ('    stations_db.py       Reads nodes_master.csv', OFFWHITE),
        ('  smartreserve_frontend/',                  YELLOW),
        ('    car_dashboard/index.html   In-Cabin UI', OFFWHITE),
        ('    kiosk_simulator/index.html Charger UI', OFFWHITE),
        ('  run_demo.py            One script starts everything', YELLOW2),
    ]
    for i, (line, color) in enumerate(tree_lines):
        pdf.c.setFillColor(color)
        pdf.c.drawString(42, y - 14 - i * 10, line)
    y -= 176

    # Tech stack in 2 columns
    pdf.c.setFont('Helvetica-Bold', 9)
    pdf.c.setFillColor(YELLOW)
    pdf.c.drawString(30, y, 'TECHNOLOGY STACK')
    y -= 12

    techs = [
        [
            ('FastAPI (Python)', 'Main server. API endpoints + WebSocket connections. Write a function — it becomes an API automatically. Port :8000.'),
            ('WebSockets (python-websockets)', 'Instant two-way communication. Car reserves → kiosk screen updates in under 100 milliseconds. No page refresh.'),
            ('PyTorch + PyTorch Geometric', 'Loads pre-trained Stanford ROLAND GNN. Runs inference to predict which stations will be congested.'),
            ('Pandas + SQLite', 'Reads 934-station nodes_master.csv. Stores reservations, PIN codes, session records during the demo.'),
        ],
        [
            ('Leaflet.js (Map)', 'Free open-source map library. Plots 934 station pins on Hyderabad street map with color-coded status. No API key needed.'),
            ('Tailwind CSS', 'Makes UI look professional and Hyundai-styled (dark mode, modern automotive feel) without weeks of custom CSS.'),
            ('Browser WebSocket API', 'Car dashboard and kiosk connect to backend WebSocket hub using built-in new WebSocket(). No library needed.'),
            ('OCPP 1.6 Protocol', 'Standard used by Tata Power and ChargeZone. ReserveNow, CancelReservation, RemoteStartTransaction commands.'),
        ],
    ]
    col_w2 = (W - 70) / 2
    for ci, col in enumerate(techs):
        cx = 30 + ci * (col_w2 + 10)
        cy = y
        for ti, (name, desc) in enumerate(col):
            card_h2 = 62
            card_y = cy - ti * (card_h2 + 6) - card_h2
            pdf.c.setFillColor(GREY2)
            pdf.c.roundRect(cx, card_y, col_w2, card_h2, 4, fill=1, stroke=0)
            pdf.c.setFillColor(YELLOW)
            pdf.c.rect(cx, card_y, 3, card_h2, fill=1, stroke=0)
            pdf.c.setFont('Helvetica-Bold', 9)
            pdf.c.setFillColor(WHITE)
            pdf.c.drawString(cx + 12, card_y + card_h2 - 16, name)
            pdf.c.setFont('Helvetica', 8)
            pdf.c.setFillColor(GREY_MID)
            wrapped = textwrap.wrap(desc, 38)
            for j, line in enumerate(wrapped[:3]):
                pdf.c.drawString(cx + 12, card_y + card_h2 - 30 - j * 11, line)

    # ── PAGE 9: 7-DAY PLAN ────────────────────────────────────────────────────
    pdf.new_page()
    y = pdf.section_header('SECTION 09 — DAY BY DAY PLAN', 'How to Build Everything in 7 Days', H - 50)

    days = [
        ('DAY 1', 'Set Up Backend Skeleton', '3-4 hrs', 'Install FastAPI, Uvicorn. Create main.py with 4 endpoints: GET /stations, POST /reserve, POST /verify-otp, WS /ws/kiosk. Load nodes_master.csv into memory using Pandas. Test all endpoints in browser.'),
        ('DAY 2', 'Build the Charger Kiosk Simulator', '4-5 hrs', 'Create kiosk/index.html. Design the charger screen with four states (Available, Reserved, Access Denied, Charging). Add PIN number keypad. Connect to backend WebSocket so screen updates automatically.'),
        ('DAY 3', 'Build the Car Dashboard', '5-6 hrs', 'Create dashboard/index.html. Add Leaflet.js map with Hyderabad coordinates. Plot all 934 stations from the database. Add battery SoC indicator, station detail panel, and the Reserve 30-Min button. Style in Hyundai dark mode.'),
        ('DAY 4', 'Connect the Reservation Flow End-to-End', '4-5 hrs', 'Wire up full journey: Click Reserve → Backend generates PIN → Backend sends WebSocket to Kiosk → Kiosk goes Blue/Locked → Dashboard shows PIN → Driver types PIN on Kiosk → Backend verifies → Kiosk unlocks.'),
        ('DAY 5', 'Add GNN Congestion Predictions', '4-5 hrs', 'Integrate gnn_predictor.py that loads pre-trained roland_final_weights_80_02.pt model. Run inference for current month. Convert output to congestion score (Low/Medium/High) per station. Display as badge on map pin.'),
        ('DAY 6', 'Add Live Charging Graph + Polish UI', '4-5 hrs', 'After unlock, simulate charging session: SoC starts at 18% and increases every 2 seconds on both screens. Plot taper curve (fast to 80%, then slower). Add 30-minute countdown timer. Make UI look premium.'),
        ('DAY 7', 'Full Demo Rehearsal + Pitch Prep', 'Full day', 'Run demo 5 times from start to finish. Open two browser windows side by side. Practice judge Q&A answers (see Section 12). Fix any bugs. Record 2-minute video demo as backup.'),
    ]
    day_h = 68
    for i, (day, title, duration, desc) in enumerate(days):
        dx = 30
        dy = y - i * (day_h + 6) - day_h
        if dy < 40:
            break
        pdf.c.setFillColor(GREY2)
        pdf.c.roundRect(dx, dy, W - 60, day_h, 4, fill=1, stroke=0)
        pdf.c.setFillColor(YELLOW)
        pdf.c.rect(dx, dy, 3, day_h, fill=1, stroke=0)
        # Day badge
        pdf.c.setFillColor(YELLOW)
        pdf.c.roundRect(dx + 12, dy + day_h - 30, 46, 22, 3, fill=1, stroke=0)
        pdf.c.setFont('Helvetica-Bold', 9)
        pdf.c.setFillColor(BLACK)
        pdf.c.drawCentredString(dx + 35, dy + day_h - 22, day)
        # Duration pill
        pdf.c.setFillColor(GREY3)
        pdf.c.roundRect(dx + 64, dy + day_h - 28, 50, 16, 3, fill=1, stroke=0)
        pdf.c.setFont('Helvetica', 7)
        pdf.c.setFillColor(YELLOW2)
        pdf.c.drawCentredString(dx + 89, dy + day_h - 22, duration)
        # Title
        pdf.c.setFont('Helvetica-Bold', 10)
        pdf.c.setFillColor(WHITE)
        pdf.c.drawString(dx + 122, dy + day_h - 20, title)
        # Desc
        pdf.c.setFont('Helvetica', 8.5)
        pdf.c.setFillColor(GREY_MID)
        wrapped = textwrap.wrap(desc, 78)
        for j, line in enumerate(wrapped[:2]):
            pdf.c.drawString(dx + 122, dy + day_h - 36 - j * 12, line)

    # ── PAGE 10: DEMO SCRIPT ──────────────────────────────────────────────────
    pdf.new_page()
    y = pdf.section_header('SECTION 10 — HACKATHON DEMO', 'Exactly What You Show Judges (90 Seconds)', H - 50)

    demo_steps = [
        ('1', 'Open Two Windows Side by Side', 'Left window: localhost:3000/car — the Hyundai in-cabin dashboard. Right window: localhost:3000/kiosk — the physical charging station. Say: "On the left is what the Hyundai Creta EV driver sees on their car screen. On the right is what is displayed at the actual DC fast charging station in Gachibowli."'),
        ('2', 'Point to the Battery Level', 'The dashboard shows battery at 18% and range at 41 km. Say: "Our system detects the car\'s battery is critically low. It has already queried our AI model and found the best available charger 1.8 km away in Gachibowli."'),
        ('3', 'Show the AI Congestion Badge', 'Click the green pin on the map. Show the station popup: 60 kW DC Fast · CCS2 · AI Demand: LOW · Predicted Queue: 0 minutes. Say: "Our Stanford ROLAND GNN — trained on 36 months of real Telangana data — predicts zero congestion for the next 2 hours."'),
        ('4', 'Click Reserve 30-Min Exclusive Slot', 'Click the button. Watch the right window instantly change from GREEN to BLUE. Say: "The moment the driver taps Reserve, our OCPP engine sends a lock command. Watch the station screen change from Available to Reserved — right now, no one else can use this charger."'),
        ('5', 'Show the PIN on the Car Screen', 'The dashboard now shows: "Slot Reserved! Your PIN: 4 8 2 9 1 0 | Time remaining: 29:58". Say: "The driver gets a one-time 6-digit PIN shown on the car screen. Nobody else knows this PIN."'),
        ('6', 'Try the Wrong PIN — See Lockout', 'On the kiosk keypad, type 111111. Hit Enter. The kiosk screen flashes: "Access Denied. Contactor remains locked. 0 kW." Say: "This proves the physical lock is real. Even if someone arrives at the station, they cannot steal this charger without the correct PIN."'),
        ('7', 'Type the Correct PIN — Start Charging!', 'Type 482910 on the kiosk keypad. Watch the screen go: "Identity Verified! Relay Closed! Charging at 60 kW." The car dashboard starts showing a live battery graph climbing from 18%. Say: "The relay closes, 60 kW of DC power flows into the battery. We can see the SoC rising live on both screens."'),
    ]
    step_h2 = 72
    for i, (num, title, script) in enumerate(demo_steps):
        sx = 30
        sy = y - i * (step_h2 + 4) - step_h2
        if sy < 50:
            break
        pdf.c.setFillColor(GREY2)
        pdf.c.roundRect(sx, sy, W - 60, step_h2, 4, fill=1, stroke=0)
        # Number
        pdf.c.setFillColor(YELLOW)
        pdf.c.circle(sx + 24, sy + step_h2/2, 14, fill=1, stroke=0)
        pdf.c.setFont('Helvetica-Bold', 12)
        pdf.c.setFillColor(BLACK)
        pdf.c.drawCentredString(sx + 24, sy + step_h2/2 - 5, num)
        # Title
        pdf.c.setFont('Helvetica-Bold', 10)
        pdf.c.setFillColor(WHITE)
        pdf.c.drawString(sx + 48, sy + step_h2 - 16, title)
        # Script
        pdf.c.setFont('Helvetica', 8.5)
        pdf.c.setFillColor(GREY_MID)
        wrapped = textwrap.wrap(script, 80)
        for j, line in enumerate(wrapped[:3]):
            pdf.c.drawString(sx + 48, sy + step_h2 - 30 - j * 13, line)

    # ── PAGE 11: COMPETITIVE EDGE ─────────────────────────────────────────────
    pdf.new_page()
    y = pdf.section_header('SECTION 11 — COMPETITIVE EDGE', 'Why This Wins the Ideathon', H - 50)

    # Comparison table
    pdf.c.setFont('Helvetica-Bold', 9)
    pdf.c.setFillColor(YELLOW)
    pdf.c.drawString(30, y, 'HEAD-TO-HEAD: WHAT OTHER TEAMS BRING vs SMARTRESERVE')
    y -= 14

    compare_rows = [
        ('Figma/PowerPoint mockup with fake screenshots', 'Fully interactive, working live prototype judges can touch'),
        ('Idea for a mobile app to find chargers', 'In-cabin experience that lives inside the car — exactly what competition asks'),
        ('Generic AI buzzwords with no real model', 'Real trained GNN model with 78% R2 accuracy on official government data'),
        ('No understanding of charging protocols', 'OCPP 1.6 protocol implementation — same standard used by Tata Power'),
        ('Theoretical reservation concept', 'Live PIN-based physical lockout demo that judges can physically try to break'),
    ]
    half = (W - 60) / 2
    crow_h = 34
    # Headers
    pdf.c.setFillColor(GREY3)
    pdf.c.rect(30, y - crow_h, half - 2, crow_h, fill=1, stroke=0)
    pdf.c.setFillColor(YELLOW)
    pdf.c.rect(30 + half + 2, y - crow_h, half - 2, crow_h, fill=1, stroke=0)
    pdf.c.setFont('Helvetica-Bold', 9)
    pdf.c.setFillColor(WHITE)
    pdf.c.drawCentredString(30 + half/2, y - crow_h + 11, 'OTHER TEAMS')
    pdf.c.setFillColor(BLACK)
    pdf.c.drawCentredString(30 + half + half/2 + 2, y - crow_h + 11, 'HYUNDAI SmartReserve')
    y -= crow_h

    for ri, (col1, col2) in enumerate(compare_rows):
        bg1 = GREY1 if ri % 2 == 0 else GREY2
        pdf.c.setFillColor(bg1)
        pdf.c.rect(30, y - crow_h, half - 2, crow_h, fill=1, stroke=0)
        pdf.c.setFillColor(colors.HexColor('#1A1500') if ri % 2 == 0 else colors.HexColor('#2B2500'))
        pdf.c.rect(30 + half + 2, y - crow_h, half - 2, crow_h, fill=1, stroke=0)
        pdf.c.setFont('Helvetica', 8)
        pdf.c.setFillColor(GREY_MID)
        pdf.c.drawString(36, y - crow_h + 12, col1[:62])
        pdf.c.setFillColor(OFFWHITE)
        pdf.c.drawString(36 + half + 2, y - crow_h + 12, col2[:62])
        pdf.c.setStrokeColor(GREY3)
        pdf.c.setLineWidth(0.3)
        pdf.c.line(30, y - crow_h, W - 30, y - crow_h)
        y -= crow_h

    y -= 16

    # 4 reasons Hyundai loves this
    pdf.c.setFont('Helvetica-Bold', 9)
    pdf.c.setFillColor(YELLOW)
    pdf.c.drawString(30, y, 'WHY HYUNDAI SPECIFICALLY LOVES THIS')
    y -= 12

    reasons = [
        ('MAKES MONEY', 'No-show fees from reservation system create a new revenue stream. Hyundai takes platform fee from every reservation. Idle fees reduce hogging at dealership chargers.'),
        ('SELLS MORE EVs', 'Range anxiety is the #1 reason Indian consumers do not buy EVs. This system eliminates that fear. More confident buyers = more Creta EV and Ioniq 5 sales.'),
        ('USES EXISTING ASSETS', 'Integrates with myHyundai app and BlueLink telematics that already exist. No new hardware in the car is needed — pure software update.'),
        ('VALUABLE DATA', 'Anonymous heat maps of where and when people charge help Hyundai and CPO partners decide where to install next fast chargers in India.'),
    ]
    rw = (W - 70) / 2
    rh = 80
    for i, (title, desc) in enumerate(reasons):
        rx = 30 + (i % 2) * (rw + 10)
        ry = y - (i // 2) * (rh + 8) - rh
        pdf.c.setFillColor(GREY2)
        pdf.c.roundRect(rx, ry, rw, rh, 4, fill=1, stroke=0)
        pdf.c.setFillColor(YELLOW)
        pdf.c.rect(rx, ry + rh - 2, rw, 2, fill=1, stroke=0)
        pdf.c.setFont('Helvetica-Bold', 9)
        pdf.c.setFillColor(YELLOW)
        pdf.c.drawString(rx + 10, ry + rh - 18, title)
        pdf.c.setFont('Helvetica', 8.5)
        pdf.c.setFillColor(GREY_MID)
        wrapped = textwrap.wrap(desc, 40)
        for j, line in enumerate(wrapped[:4]):
            pdf.c.drawString(rx + 10, ry + rh - 34 - j * 12, line)

    # ── PAGE 12: JUDGE Q&A ────────────────────────────────────────────────────
    pdf.new_page()
    y = pdf.section_header('SECTION 12 — BE PREPARED', 'Questions Judges Will Ask & Your Answers', H - 50)

    qas = [
        ('Q1', '"How does the physical charger know to lock? You cannot install software on real chargers."',
         'Every modern DC fast charger in India already runs OCPP 1.6 or 2.0 firmware by law — required by the Ministry of Power\'s 2024 BEE guidelines. Our backend acts as the Central System (CSMS) and sends standard ReserveNow commands over WebSocket to the charger\'s IP address. The charger\'s own firmware handles the relay lockout. We are not modifying any hardware — we are using the standard protocol the charger already supports.'),
        ('Q2', '"What if the CPO (Tata Power, ChargeZone) refuses to give you API access?"',
         'That is why Hyundai is the right partner for this. Hyundai already has signed MoUs with ChargeZone, Statiq, and Tata Power for their dealership fast charging network. Hyundai can negotiate API access as part of the partnership terms. For the prototype, we connect to simulated OCPP charger nodes that behave identically to real hardware.'),
        ('Q3', '"Your GNN has 78% accuracy — what about the other 22%? Can we trust it?"',
         'We always display the prediction as a range with a confidence label, not a hard promise. The UI shows "Predicted Queue: 0–5 minutes (High Confidence)" or "20–40 minutes (Low Confidence)." This is honest with the driver. We also show the timestamp of the last known live status so the driver knows exactly how fresh the data is.'),
        ('Q4', '"This is just an app. Why is this an in-vehicle experience?"',
         'The car itself detects the battery level and triggers the system automatically — the driver does not have to open any app. The interface runs on the factory-installed AVNT infotainment touchscreen using Hyundai\'s ccNC connected car platform. The driver uses steering wheel controls or voice to confirm the reservation without looking away from the road. This is exactly what BlueLink\'s roadmap already supports.'),
        ('Q5', '"Is this even legal — deducting money without completing a transaction?"',
         'We use UPI AutoPay mandates or card pre-authorization holds — not actual deductions. The money only moves on a confirmed no-show, exactly like hotel booking cancellation fees which are fully legal under RBI guidelines. We would work with Razorpay or PayU to implement the correct mandate flow before a real launch.'),
    ]
    qa_h = 98
    for i, (qnum, question, answer) in enumerate(qas):
        qx = 30
        qy = y - i * (qa_h + 6) - qa_h
        if qy < 40:
            break
        pdf.c.setFillColor(GREY2)
        pdf.c.roundRect(qx, qy, W - 60, qa_h, 4, fill=1, stroke=0)
        pdf.c.setFillColor(YELLOW)
        pdf.c.rect(qx, qy, 3, qa_h, fill=1, stroke=0)
        # Q badge
        pdf.c.setFillColor(YELLOW)
        pdf.c.roundRect(qx + 10, qy + qa_h - 26, 28, 18, 3, fill=1, stroke=0)
        pdf.c.setFont('Helvetica-Bold', 9)
        pdf.c.setFillColor(BLACK)
        pdf.c.drawCentredString(qx + 24, qy + qa_h - 20, qnum)
        # Question
        pdf.c.setFont('Helvetica-Bold', 9)
        pdf.c.setFillColor(WHITE)
        q_wrapped = textwrap.wrap(question, 76)
        for j, line in enumerate(q_wrapped[:2]):
            pdf.c.drawString(qx + 48, qy + qa_h - 16 - j * 12, line)
        # Answer label
        pdf.c.setFont('Helvetica-Bold', 7)
        pdf.c.setFillColor(YELLOW)
        pdf.c.drawString(qx + 10, qy + qa_h - 48, 'YOUR ANSWER:')
        # Answer text
        pdf.c.setFont('Helvetica', 8.5)
        pdf.c.setFillColor(GREY_MID)
        a_wrapped = textwrap.wrap(answer, 86)
        for j, line in enumerate(a_wrapped[:3]):
            pdf.c.drawString(qx + 10, qy + qa_h - 60 - j * 12, line)

    # ── PAGE 13: SUBMISSION + CLOSING ─────────────────────────────────────────
    pdf.new_page()
    y = pdf.section_header('SECTION 13 — SUBMISSION PITCH', 'Your Ideathon Submission Outline', H - 50)

    slides = [
        ('1', 'Cover', 'Hyundai SmartReserve · Category 2: AI-Powered & Digital In-Vehicle Experience · Track B: Technology & Prototype'),
        ('2', 'The Problem', '4 pain points with real stats: No live status, phone while driving, no reservation, ICE vehicle blocking'),
        ('3', 'The Solution', 'One sentence + the system flow diagram (Car → Cloud → Charger)'),
        ('4', 'The AI Brain', 'GNN architecture, 78% accuracy, 934 real stations, 36 months of data'),
        ('5', 'The Lock Mechanism', 'OCPP 1.6 ReserveNow flow, charger state diagram, PIN lockout visual'),
        ('6', 'The In-Vehicle Experience', 'Screenshot of car dashboard UI — show how the driver never touches their phone'),
        ('7', 'Live Demo', 'Run the live prototype: Reserve → Kiosk locks → Wrong PIN → Correct PIN → Charging starts'),
        ('8', 'Why Hyundai', 'Existing CPO partnerships, myHyundai/BlueLink integration, dealership charger monetization'),
        ('9', 'Business Value', 'Revenue from no-show fees, more EV sales, data for CPO expansion decisions'),
        ('10', 'Roadmap', 'Phase 1: Demo (done) → Phase 2: Pilot 5 dealerships in Hyderabad → Phase 3: All India rollout'),
    ]
    slide_h = 36
    for i, (num, title, content) in enumerate(slides):
        slx = 30
        sly = y - i * (slide_h + 4) - slide_h
        if sly < 180:
            break
        bg = GREY2 if i % 2 == 0 else GREY1
        pdf.c.setFillColor(bg)
        pdf.c.roundRect(slx, sly, W - 60, slide_h, 3, fill=1, stroke=0)
        # Slide number
        pdf.c.setFillColor(YELLOW)
        pdf.c.roundRect(slx + 8, sly + 7, 22, 22, 2, fill=1, stroke=0)
        pdf.c.setFont('Helvetica-Bold', 10)
        pdf.c.setFillColor(BLACK)
        pdf.c.drawCentredString(slx + 19, sly + 12, num)
        # Title
        pdf.c.setFont('Helvetica-Bold', 10)
        pdf.c.setFillColor(WHITE)
        pdf.c.drawString(slx + 38, sly + slide_h - 14, title)
        # Content
        pdf.c.setFont('Helvetica', 8.5)
        pdf.c.setFillColor(GREY_MID)
        pdf.c.drawString(slx + 38, sly + 8, content[:80])

    y = 220

    # Winning formula box
    pdf.c.setFillColor(colors.HexColor('#1A1500'))
    pdf.c.roundRect(30, y - 56, W - 60, 58, 6, fill=1, stroke=0)
    pdf.c.setFillColor(YELLOW)
    pdf.c.rect(30, y, W - 60, 2, fill=1, stroke=0)
    pdf.c.setFont('Helvetica-Bold', 9)
    pdf.c.setFillColor(YELLOW)
    pdf.c.drawString(42, y - 18, 'THE WINNING FORMULA')
    pdf.c.setFont('Helvetica', 9)
    pdf.c.setFillColor(OFFWHITE)
    formula = 'Real Data (BEE + Parivahan) + Real AI (GNN 78% R2) + Real Protocol (OCPP 1.6) + Live Interactive Prototype = The team that built the most complete, credible, and real submission in the room.'
    wrapped = textwrap.wrap(formula, 86)
    for j, line in enumerate(wrapped[:2]):
        pdf.c.drawString(42, y - 34 - j * 14, line)

    # Key dates + closing
    y = 152
    dates = [
        ('Preliminary Winner', 'October 30, 2026'),
        ('Final Round', 'December 16, 2026 · Delhi'),
        ('Prize', 'Rs.1 Lakh finalist + Rs.3 Lakh Grand Prize'),
    ]
    dw = (W - 60) / 3
    for i, (label, date) in enumerate(dates):
        dx = 30 + i * (dw + 5)
        dy = y - 52
        pdf.c.setFillColor(GREY2)
        pdf.c.roundRect(dx, dy, dw, 52, 4, fill=1, stroke=0)
        pdf.c.setFillColor(YELLOW)
        pdf.c.rect(dx, dy + 50, dw, 2, fill=1, stroke=0)
        pdf.c.setFont('Helvetica-Bold', 8)
        pdf.c.setFillColor(YELLOW)
        pdf.c.drawString(dx + 8, dy + 36, label.upper())
        pdf.c.setFont('Helvetica-Bold', 9)
        pdf.c.setFillColor(WHITE)
        pdf.c.drawString(dx + 8, dy + 18, date)

    # Final tagline
    pdf.c.setFont('Helvetica-Bold', 14)
    pdf.c.setFillColor(YELLOW)
    pdf.c.drawCentredString(W/2, 70, 'Hyundai SmartReserve')
    pdf.c.setFont('Helvetica', 9)
    pdf.c.setFillColor(GREY_MID)
    pdf.c.drawCentredString(W/2, 54, 'Category 2  ·  Track B  ·  Technology & Prototype  ·  Hyundai CoE Mobility Innovation Ideathon 2026')

    pdf.save()
    print(f'PDF successfully saved to {output_path}')


if __name__ == '__main__':
    build()
