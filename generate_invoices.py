#!/usr/bin/env python3
"""
Generate 12 synthetic Moroccan B2B invoices (PDF) + a ground-truth JSON file.

Run from the project root:
    pip install reportlab
    python generate_invoices.py

Output:
    data/invoices/invoice_XX_<supplier>.pdf   (12 files)
    data/ground_truth.json

Mix:      8 French + 4 English invoices
Layouts:  3 templates (A = classic grid, B = modern banner, C = minimal serif), 4 invoices each
Faults:   3 deliberately faulty invoices (see FAULTS below)
All suppliers / clients are fictional. Numbers are random (seeded, reproducible).
"""

import json
import random
import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

OUT_DIR = Path("data/invoices")
GT_PATH = Path("data/ground_truth.json")
SEED = 20260930
TVA_RATE = Decimal("0.20")

# Invoice indices (0-based) that are written in English; all others are French.
ENGLISH_IDX = {1, 5, 6, 10}

# Deliberately faulty invoices: index -> fault type.
FAULTS = {
    2: "ttc_mismatch",            # French,  template C
    6: "ice_14_digits",           # English, template A
    7: "missing_invoice_number",  # French,  template B
}

# Number of line items per invoice (covers 2..5).
LINE_COUNTS = [3, 4, 2, 5, 3, 4, 2, 5, 3, 4, 2, 3]

INVOICE_NO_FORMATS = ["FA-{y}-{n:04d}", "INV-{n:06d}", "{y}/{n:04d}", "F{y}{n:05d}"]

# --------------------------------------------------------------------------
# Fictional suppliers. Item tuple:
#   (description_fr, description_en, price_min, price_max, qty_min, qty_max)
# --------------------------------------------------------------------------
SUPPLIERS = [
    dict(name="Atlas Fournitures Industrielles SARL", address="Zone Industrielle Ain Sebaa, Lot 214",
         city="20250 Casablanca", area="22", items=[
        ("Gants de sécurité renforcés (paire)", "Reinforced safety gloves (pair)", 18, 35, 50, 300),
        ("Casque de chantier homologué", "Certified hard hat", 45, 85, 10, 60),
        ("Roulement à billes 6205-2RS", "6205-2RS ball bearing", 60, 120, 10, 80),
        ("Huile hydraulique ISO 46 (fût 20 L)", "ISO 46 hydraulic oil (20 L drum)", 650, 900, 1, 8),
        ("Chaussures de sécurité S3", "S3 safety boots", 220, 380, 5, 40)]),
    dict(name="Maghreb Logistique & Transport SA", address="Route de Rabat, Km 7",
         city="90000 Tanger", area="39", items=[
        ("Transport palettes Casablanca - Tanger", "Pallet freight Casablanca - Tangier", 3500, 6500, 1, 4),
        ("Location camion frigorifique (jour)", "Refrigerated truck rental (per day)", 2200, 3200, 1, 6),
        ("Stockage palette (mois)", "Pallet storage (per month)", 85, 140, 20, 120),
        ("Frais de dédouanement", "Customs clearance fee", 600, 1500, 1, 3),
        ("Manutention chariot élévateur (heure)", "Forklift handling (hour)", 180, 300, 4, 30)]),
    dict(name="Sahara Tech Solutions SARL AU", address="Technopolis, Bâtiment B4, Sala Al Jadida",
         city="11100 Rabat", area="37", items=[
        ("Maintenance serveur mensuelle", "Monthly server maintenance", 2500, 6000, 1, 3),
        ("Licence bureautique cloud (util./an)", "Cloud office licence (user/year)", 1400, 1900, 5, 40),
        ("Switch réseau 24 ports PoE", "24-port PoE network switch", 2800, 4500, 1, 5),
        ("Câblage réseau Cat6 (point)", "Cat6 network cabling (per drop)", 350, 600, 10, 60),
        ("Support technique (heure)", "Technical support (hour)", 400, 650, 3, 20)]),
    dict(name="Nador Emballages SARL", address="Quartier Industriel Selouane, N° 38",
         city="62000 Nador", area="36", items=[
        ("Carton ondulé 60x40x40 cm", "Corrugated box 60x40x40 cm", 4.5, 9, 200, 2000),
        ("Film étirable 50 cm (bobine)", "Stretch film 50 cm (roll)", 55, 95, 20, 150),
        ("Palette bois EUR 1200x800", "EUR wooden pallet 1200x800", 55, 85, 20, 150),
        ("Ruban adhésif (carton de 36)", "Packing tape (box of 36)", 180, 260, 3, 30),
        ("Sachets alimentaires (lot de 1000)", "Food-grade bags (pack of 1000)", 70, 130, 10, 80)]),
    dict(name="Dar Al Imprimerie SARL", address="12 Rue Abou Hassan Al Marini, Ville Nouvelle",
         city="30000 Fès", area="35", items=[
        ("Impression brochure A4 (1000 ex.)", "A4 brochure printing (1,000 copies)", 1800, 4200, 1, 3),
        ("Cartes de visite (lot de 500)", "Business cards (pack of 500)", 120, 220, 2, 10),
        ("Affiches A2 (lot de 50)", "A2 posters (pack of 50)", 650, 1100, 1, 6),
        ("Étiquettes adhésives (rouleau)", "Adhesive labels (roll)", 60, 120, 10, 80),
        ("Catalogue relié 40 pages", "Bound catalogue, 40 pages", 38, 75, 100, 500)]),
    dict(name="Souss Agro Distribution SA", address="Parc Industriel Ait Melloul, Lot 9",
         city="80000 Agadir", area="28", items=[
        ("Engrais NPK 15-15-15 (sac 50 kg)", "NPK 15-15-15 fertiliser (50 kg bag)", 280, 360, 20, 200),
        ("Tuyau goutte-à-goutte (rouleau 500 m)", "Drip irrigation pipe (500 m roll)", 900, 1500, 2, 20),
        ("Filet d'ombrage 50% (rouleau)", "50% shade net (roll)", 1200, 2000, 1, 10),
        ("Pompe immergée 1,5 kW", "1.5 kW submersible pump", 2400, 3800, 1, 6),
        ("Semences de tomate (boîte)", "Tomato seeds (tin)", 450, 780, 2, 25)]),
    dict(name="Rif Building Materials SARL", address="Avenue des FAR, Zone Industrielle Mghogha",
         city="93000 Tétouan", area="39", items=[
        ("Ciment CPJ 45 (sac 50 kg)", "CPJ 45 cement (50 kg bag)", 60, 80, 100, 800),
        ("Fer à béton HA 10 (barre 12 m)", "HA 10 rebar (12 m bar)", 45, 70, 100, 600),
        ("Carreaux céramique 40x40 (m2)", "Ceramic tiles 40x40 (per sq m)", 85, 160, 30, 300),
        ("Peinture façade (seau 25 kg)", "Exterior paint (25 kg bucket)", 420, 650, 4, 40),
        ("Brique creuse 12 trous (unité)", "Hollow brick, 12 holes (unit)", 2.5, 4, 1000, 8000)]),
    dict(name="Marrakech Office Pro SARL", address="Immeuble Les Jardins, Av. Mohammed VI",
         city="40000 Marrakech", area="24", items=[
        ("Ramette papier A4 80 g", "A4 paper ream 80 gsm", 28, 45, 50, 400),
        ("Toner imprimante noir", "Black printer toner", 450, 900, 2, 15),
        ("Chaise de bureau ergonomique", "Ergonomic office chair", 650, 1400, 4, 30),
        ("Bureau 140 cm", "Desk 140 cm", 1100, 2200, 2, 20),
        ("Classeurs à levier (lot de 10)", "Lever arch files (pack of 10)", 80, 140, 5, 40)]),
    dict(name="Oriental Cleaning Services SARL", address="Bd Mohammed V, Résidence Al Massira, N° 5",
         city="60000 Oujda", area="36", items=[
        ("Nettoyage de bureaux (mois)", "Office cleaning (month)", 4500, 9500, 1, 3),
        ("Désinfection des locaux (intervention)", "Premises disinfection (visit)", 1200, 2600, 1, 4),
        ("Produit sol concentré (bidon 5 L)", "Concentrated floor cleaner (5 L)", 90, 160, 5, 40),
        ("Lavage de vitres (forfait)", "Window cleaning (flat fee)", 800, 1800, 1, 3),
        ("Distributeur de savon mural", "Wall-mounted soap dispenser", 120, 240, 5, 30)]),
    dict(name="Meknès Pièces Auto SARL", address="Route d'El Hajeb, Km 3",
         city="50000 Meknès", area="35", items=[
        ("Plaquettes de frein avant (jeu)", "Front brake pads (set)", 220, 420, 5, 40),
        ("Filtre à huile", "Oil filter", 35, 75, 20, 150),
        ("Batterie 70 Ah", "70 Ah battery", 750, 1100, 2, 15),
        ("Amortisseur avant", "Front shock absorber", 380, 700, 4, 24),
        ("Kit courroie de distribution", "Timing belt kit", 450, 900, 2, 20)]),
    dict(name="Anfa Consulting Group SA", address="Boulevard d'Anfa, Immeuble Atlas, 5e étage",
         city="20060 Casablanca", area="22", items=[
        ("Audit comptable (jour/homme)", "Accounting audit (man-day)", 3500, 5500, 2, 15),
        ("Conseil stratégique (forfait)", "Strategy consulting (flat fee)", 12000, 30000, 1, 2),
        ("Formation management (journée)", "Management training (day)", 6000, 9000, 1, 4),
        ("Étude de marché (forfait)", "Market study (flat fee)", 15000, 28000, 1, 1),
        ("Rapport de conformité fiscale", "Tax compliance report", 4000, 8000, 1, 3)]),
    dict(name="Zagora Solar Energy SARL", address="Zone Industrielle Tabounte, Lot 17",
         city="45000 Ouarzazate", area="24", items=[
        ("Panneau solaire 450 W", "450 W solar panel", 1800, 2600, 10, 80),
        ("Onduleur hybride 5 kW", "5 kW hybrid inverter", 7500, 12000, 1, 4),
        ("Batterie lithium 5 kWh", "5 kWh lithium battery", 11000, 16000, 1, 4),
        ("Installation et mise en service", "Installation and commissioning", 3000, 9000, 1, 3),
        ("Câble solaire 6 mm (100 m)", "6 mm solar cable (100 m)", 650, 1000, 1, 6)]),
]

CLIENTS = [
    ("Groupe Chaouia Distribution SA", "Bd Mohammed V, 26000 Settat"),
    ("Hôtel Riad Zitoun SARL", "Derb Sidi Ali, Médina, 40000 Marrakech"),
    ("Clinique Al Amal SA", "Av. des FAR, 10000 Rabat"),
    ("Coopérative Agricole Doukkala", "Route d'El Jadida, 24350 Sidi Bennour"),
    ("Cimenterie du Nord SA", "Zone Industrielle, 90000 Tanger"),
    ("Restaurant Le Jardin SARL", "Rue Ibn Battouta, 30000 Fès"),
    ("Atelier Zellige d'Or SARL", "Quartier Industriel, 11000 Salé"),
    ("Laiterie Tafilalet SA", "Route de Rissani, 52000 Errachidia"),
    ("Pharmacie Centrale du Sud SARL", "Av. Hassan II, 70000 Laâyoune"),
    ("Société Bouregreg Immobilier SA", "Av. Annakhil, Hay Riad, 10100 Rabat"),
    ("Transports Oum Rbia SARL", "Bd Zerktouni, 23000 Béni Mellal"),
    ("École Supérieure Al Khawarizmi", "Route de Sefrou, 30050 Fès"),
]

LABELS = {
    "fr": dict(invoice="FACTURE", title="Facture", number="Facture N°", date="Date", due="Échéance",
               client="Facturé à", desc="Désignation", qty="Qté", unit="P.U. HT (MAD)",
               line_total="Montant HT (MAD)", ht="Total HT", tva="TVA 20 %", ttc="Total TTC",
               pay="Conditions de paiement : 30 jours à compter de la date de facture",
               rib="RIB", tel="Tél"),
    "en": dict(invoice="INVOICE", title="Invoice", number="Invoice No.", date="Date", due="Due date",
               client="Bill to", desc="Description", qty="Qty", unit="Unit price (MAD)",
               line_total="Amount (MAD)", ht="Subtotal (excl. VAT)", tva="VAT 20%", ttc="Total (incl. VAT)",
               pay="Payment terms: net 30 days from invoice date",
               rib="Bank account (RIB)", tel="Tel"),
}

MONTHS = {
    "fr": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
           "septembre", "octobre", "novembre", "décembre"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"],
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def q2(d):
    return Decimal(d).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fmt(d, lang):
    """FR: 12 345,67   EN: 12,345.67"""
    s = f"{d:,.2f}"
    if lang == "fr":
        s = s.replace(",", " ").replace(".", ",")
    return s


def money(d, lang):
    return f"{fmt(d, lang)} MAD"


def fmt_date(d, lang, style):
    if style == "numeric":
        return d.strftime("%d/%m/%Y")
    if style == "dash":
        return d.strftime("%d-%m-%Y")
    if lang == "fr":
        return f"{d.day} {MONTHS['fr'][d.month - 1]} {d.year}"
    return f"{MONTHS['en'][d.month - 1]} {d.day}, {d.year}"


def slugify(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def fit_size(text, font, size, max_w):
    while stringWidth(text, font, size) > max_w and size > 6:
        size -= 0.5
    return size


def make_ice(rng):
    """15-digit ICE (Identifiant Commun de l'Entreprise)."""
    return f"00{rng.randint(0, 9999999):07d}000{rng.randint(0, 999):03d}"


# --------------------------------------------------------------------------
# Data generation
# --------------------------------------------------------------------------
def build_invoice(i, sup, rng):
    lang = "en" if i in ENGLISH_IDX else "fr"
    issue = date(2025, 10, 1) + timedelta(days=rng.randint(0, 330))

    items = []
    for fr, en, pmin, pmax, qmin, qmax in rng.sample(sup["items"], k=LINE_COUNTS[i]):
        qty = rng.randint(qmin, qmax)
        unit = q2(Decimal(rng.randint(round(pmin * 2), round(pmax * 2))) / 2)
        items.append({"description": en if lang == "en" else fr, "quantity": qty,
                      "unit_price": unit, "line_total": q2(unit * qty)})

    ht = sum((it["line_total"] for it in items), Decimal("0"))
    tva = q2(ht * TVA_RATE)
    ttc = ht + tva

    ice = make_ice(rng)
    invoice_number = INVOICE_NO_FORMATS[i % 4].format(y=issue.year, n=rng.randint(12, 950))
    client_name, client_addr = CLIENTS[i]

    # ---- deliberate faults (recorded exactly as printed) ----
    fault = FAULTS.get(i)
    error = None
    if fault == "ttc_mismatch":
        printed = ttc + Decimal("75.00")
        error = (f"TOTAL_MISMATCH: total_ht ({ht}) + tva ({tva}) = {ht + tva}, "
                 f"but the printed total_ttc is {printed}")
        ttc = printed
    elif fault == "ice_14_digits":
        ice = ice[:8] + ice[9:]  # one digit dropped -> 14 digits
        error = "INVALID_ICE: printed ICE has 14 digits, a valid ICE has exactly 15 digits"
    elif fault == "missing_invoice_number":
        invoice_number = ""
        error = "MISSING_INVOICE_NUMBER: no invoice number is printed on the document"

    return {
        "index": i, "language": lang, "template": "ABC"[i % 3],
        "supplier_name": sup["name"], "address": sup["address"], "city": sup["city"],
        "phone": f"05 {sup['area']} {rng.randint(10, 99)} {rng.randint(10, 99)} {rng.randint(10, 99)}",
        "ice": ice, "rc": str(rng.randint(10000, 499999)), "if_no": str(rng.randint(10000000, 49999999)),
        "rib": f"{rng.choice(['007', '011', '021', '181'])} {rng.randint(100, 999)} "
               f"{rng.randint(0, 10**16 - 1):016d} {rng.randint(10, 99)}",
        "client_name": client_name, "client_address": client_addr, "client_ice": make_ice(rng),
        "invoice_number": invoice_number, "date": issue, "due_date": issue + timedelta(days=30),
        "line_items": items, "total_ht": ht, "tva": tva, "total_ttc": ttc,
        "expected_error": error,
    }


def find_errors(inv):
    """Independent validator used to double-check the generated data."""
    errs = set()
    if len(inv["ice"]) != 15 or not inv["ice"].isdigit():
        errs.add("ice")
    if not inv["invoice_number"]:
        errs.add("invoice_number")
    if inv["total_ht"] + inv["tva"] != inv["total_ttc"]:
        errs.add("totals")
    assert sum(it["line_total"] for it in inv["line_items"]) == inv["total_ht"]
    assert q2(inv["total_ht"] * TVA_RATE) == inv["tva"]
    return errs


def to_ground_truth(inv):
    gt = {
        "supplier_name": inv["supplier_name"],
        "ice": inv["ice"],
        "invoice_number": inv["invoice_number"] or None,
        "date": inv["date"].isoformat(),
        "due_date": inv["due_date"].isoformat(),
        "language": inv["language"],
        "template": inv["template"],
        "currency": "MAD",
        "client_name": inv["client_name"],
        "client_ice": inv["client_ice"],
        "line_items": [
            {"description": it["description"], "quantity": it["quantity"],
             "unit_price": float(it["unit_price"]), "line_total": float(it["line_total"])}
            for it in inv["line_items"]
        ],
        "total_ht": float(inv["total_ht"]),
        "tva": float(inv["tva"]),
        "total_ttc": float(inv["total_ttc"]),
    }
    if inv["expected_error"]:
        gt["expected_error"] = inv["expected_error"]
    return gt


# --------------------------------------------------------------------------
# Shared drawing blocks
# --------------------------------------------------------------------------
def draw_table(c, inv, L, x, y_top, widths, *, font, bold, size, row_h, head_fill, head_text,
               grid, zebra=None, rule=colors.black):
    """Line-items table. grid: 'full' | 'h' | 'none'. Returns y of the table bottom."""
    lang = inv["language"]
    pad = 2.5 * mm
    n = len(inv["line_items"])
    total_w = sum(widths)
    xs = [x]
    for w in widths:
        xs.append(xs[-1] + w)
    bottom = y_top - row_h * (n + 1)

    if head_fill is not None:
        c.setFillColor(head_fill)
        c.rect(x, y_top - row_h, total_w, row_h, stroke=0, fill=1)
    if zebra is not None:
        c.setFillColor(zebra)
        for k in range(n):
            if k % 2 == 1:
                c.rect(x, y_top - row_h * (k + 2), total_w, row_h, stroke=0, fill=1)

    def baseline(row):  # row is 1-based, row 1 = header
        return y_top - row_h * (row - 0.5) - size * 0.35

    def draw_row(row, cells, fnt, col):
        c.setFillColor(col)
        c.setFont(fnt, size)
        c.drawString(xs[0] + pad, baseline(row), cells[0])
        for k in range(1, 4):
            c.drawRightString(xs[k + 1] - pad, baseline(row), cells[k])

    draw_row(1, [L["desc"], L["qty"], L["unit"], L["line_total"]], bold, head_text)
    for k, it in enumerate(inv["line_items"]):
        assert stringWidth(it["description"], font, size) <= widths[0] - 2 * pad, it["description"]
        draw_row(k + 2, [it["description"], str(it["quantity"]), fmt(it["unit_price"], lang),
                         fmt(it["line_total"], lang)], font, colors.black)

    c.setStrokeColor(rule)
    if grid == "full":
        c.setLineWidth(0.6)
        c.rect(x, bottom, total_w, row_h * (n + 1), stroke=1, fill=0)
        for r in range(1, n + 1):
            c.line(x, y_top - row_h * r, x + total_w, y_top - row_h * r)
        for xv in xs[1:-1]:
            c.line(xv, y_top, xv, bottom)
    elif grid == "h":
        c.setLineWidth(0.8)
        c.line(x, y_top, x + total_w, y_top)
        c.line(x, y_top - row_h, x + total_w, y_top - row_h)
        c.setLineWidth(0.3)
        for r in range(2, n + 1):
            c.line(x, y_top - row_h * r, x + total_w, y_top - row_h * r)
        c.setLineWidth(0.8)
        c.line(x, bottom, x + total_w, bottom)
    else:
        c.setLineWidth(0.6)
        c.line(x, bottom, x + total_w, bottom)
    return bottom


def draw_totals(c, inv, L, x_right, y_top, w, *, font, bold, size, row_h, style,
                ttc_fill=None, ttc_text=colors.black, rule=colors.black):
    """Total HT / TVA / TTC block. style: 'box' | 'lines'."""
    lang = inv["language"]
    pad = 2.5 * mm
    x = x_right - w
    rows = [(L["ht"], inv["total_ht"]), (L["tva"], inv["tva"]), (L["ttc"], inv["total_ttc"])]
    for k, (label, value) in enumerate(rows):
        top = y_top - row_h * k
        last = k == 2
        if last and ttc_fill is not None:
            c.setFillColor(ttc_fill)
            c.rect(x, top - row_h, w, row_h, stroke=0, fill=1)
        c.setStrokeColor(rule)
        if style == "box":
            c.setLineWidth(0.6)
            c.rect(x, top - row_h, w, row_h, stroke=1, fill=0)
        else:
            c.setLineWidth(0.4)
            c.line(x, top, x_right, top)
            if last:
                c.setLineWidth(1.0)
                c.line(x, top - row_h, x_right, top - row_h)
        fsize = size + (1.5 if last else 0)
        c.setFillColor(ttc_text if last else colors.black)
        c.setFont(bold if last else font, fsize)
        by = top - row_h / 2 - fsize * 0.35
        c.drawString(x + pad, by, label)
        c.drawRightString(x_right - pad, by, money(value, lang))
    return y_top - 3 * row_h


# --------------------------------------------------------------------------
# Template A: classic, black & white, boxed meta + full grid table
# --------------------------------------------------------------------------
def template_a(c, inv, L):
    W, H = A4
    m = 18 * mm
    lang = inv["language"]
    d = lambda dt: fmt_date(dt, lang, "numeric")

    y = H - m - 4 * mm
    size = fit_size(inv["supplier_name"], "Helvetica-Bold", 14, 95 * mm)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", size)
    c.drawString(m, y, inv["supplier_name"])
    c.setFont("Helvetica", 8.5)
    for line in [inv["address"], inv["city"], f'{L["tel"]} : {inv["phone"]}', f'ICE : {inv["ice"]}',
                 f'RC : {inv["rc"]}  |  IF : {inv["if_no"]}']:
        y -= 11
        c.drawString(m, y, line)
    sup_bottom = y

    c.setFont("Helvetica-Bold", 24)
    c.drawRightString(W - m, H - m - 6 * mm, L["invoice"])

    bw, bh = 68 * mm, 24 * mm
    bx, by = W - m - bw, H - m - 12 * mm - bh
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.8)
    c.rect(bx, by, bw, bh)
    rows = [(L["number"], inv["invoice_number"]), (L["date"], d(inv["date"])), (L["due"], d(inv["due_date"]))]
    for k, (lab, val) in enumerate(rows):
        row_bottom = by + bh - (k + 1) * 8 * mm
        if k:
            c.line(bx, row_bottom + 8 * mm, bx + bw, row_bottom + 8 * mm)
        c.setFont("Helvetica", 8.5)
        c.drawString(bx + 3 * mm, row_bottom + 2.7 * mm, lab)
        c.setFont("Helvetica-Bold", 9.5)
        c.drawRightString(bx + bw - 3 * mm, row_bottom + 2.7 * mm, val)

    top = min(sup_bottom, by) - 9 * mm
    c.setFont("Helvetica-Bold", 8)
    c.drawString(m, top, L["client"].upper())
    c.setLineWidth(0.6)
    c.rect(m, top - 3 * mm - 22 * mm, 95 * mm, 22 * mm)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(m + 3 * mm, top - 10 * mm, inv["client_name"])
    c.setFont("Helvetica", 8.5)
    c.drawString(m + 3 * mm, top - 15 * mm, inv["client_address"])
    c.drawString(m + 3 * mm, top - 20 * mm, f'ICE : {inv["client_ice"]}')

    widths = [w * mm for w in (84, 16, 36, 38)]
    t_bottom = draw_table(c, inv, L, m, top - 25 * mm - 14 * mm, widths, font="Helvetica",
                          bold="Helvetica-Bold", size=9, row_h=8 * mm,
                          head_fill=colors.Color(0.88, 0.88, 0.88), head_text=colors.black, grid="full")
    draw_totals(c, inv, L, W - m, t_bottom - 8 * mm, 78 * mm, font="Helvetica", bold="Helvetica-Bold",
                size=9, row_h=8 * mm, style="box", ttc_fill=colors.Color(0.88, 0.88, 0.88))

    c.setStrokeColor(colors.grey)
    c.setLineWidth(0.5)
    c.line(m, 26 * mm, W - m, 26 * mm)
    c.setFillColor(colors.Color(0.3, 0.3, 0.3))
    c.setFont("Helvetica", 8)
    c.drawString(m, 21 * mm, L["pay"])
    c.drawString(m, 16.5 * mm, f'{L["rib"]} : {inv["rib"]}')


# --------------------------------------------------------------------------
# Template B: modern colour banner, zebra table, filled TTC bar
# --------------------------------------------------------------------------
def template_b(c, inv, L):
    W, H = A4
    m = 18 * mm
    lang = inv["language"]
    accent = colors.HexColor("#0B5563")
    tint = colors.HexColor("#EAF3F4")
    grey = colors.HexColor("#5A6B70")
    d = lambda dt: fmt_date(dt, lang, "long")

    band = 40 * mm
    c.setFillColor(accent)
    c.rect(0, H - band, W, band, stroke=0, fill=1)
    c.setFillColor(colors.white)
    size = fit_size(inv["supplier_name"], "Helvetica-Bold", 17, 110 * mm)
    c.setFont("Helvetica-Bold", size)
    c.drawString(m, H - 15 * mm, inv["supplier_name"])
    c.setFont("Helvetica", 8)
    y = H - 21 * mm
    for line in [f'{inv["address"]}, {inv["city"]}',
                 f'{L["tel"]} : {inv["phone"]}   |   ICE : {inv["ice"]}',
                 f'RC : {inv["rc"]}   |   IF : {inv["if_no"]}']:
        c.drawString(m, y, line)
        y -= 10
    c.setFont("Helvetica-Bold", 24)
    c.drawRightString(W - m, H - 16 * mm, L["invoice"])

    y0 = H - band - 12 * mm
    c.setFillColor(accent)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(m, y0, L["client"].upper())
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(m, y0 - 14, inv["client_name"])
    c.setFont("Helvetica", 9)
    c.drawString(m, y0 - 26, inv["client_address"])
    c.drawString(m, y0 - 38, f'ICE : {inv["client_ice"]}')

    mx = W - m - 78 * mm
    rows = [(L["number"], inv["invoice_number"]), (L["date"], d(inv["date"])), (L["due"], d(inv["due_date"]))]
    for k, (lab, val) in enumerate(rows):
        ry = y0 - 4 - k * 14
        c.setFillColor(grey)
        c.setFont("Helvetica", 8.5)
        c.drawString(mx, ry, lab)
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 10)
        c.drawRightString(W - m, ry, val)
        c.setStrokeColor(colors.HexColor("#C9D6D9"))
        c.setLineWidth(0.4)
        c.line(mx, ry - 4, W - m, ry - 4)

    widths = [w * mm for w in (84, 16, 36, 38)]
    t_bottom = draw_table(c, inv, L, m, y0 - 38 - 14 * mm, widths, font="Helvetica", bold="Helvetica-Bold",
                          size=9, row_h=9 * mm, head_fill=accent, head_text=colors.white, grid="none",
                          zebra=tint, rule=accent)
    draw_totals(c, inv, L, W - m, t_bottom - 8 * mm, 82 * mm, font="Helvetica", bold="Helvetica-Bold",
                size=9, row_h=8.5 * mm, style="lines", ttc_fill=accent, ttc_text=colors.white,
                rule=colors.HexColor("#C9D6D9"))

    c.setFillColor(accent)
    c.rect(0, 0, W, 18 * mm, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica", 8)
    c.drawString(m, 10.5 * mm, L["pay"])
    c.drawString(m, 6 * mm, f'{L["rib"]} : {inv["rib"]}')


# --------------------------------------------------------------------------
# Template C: minimal serif, centred header, horizontal rules only
# --------------------------------------------------------------------------
def template_c(c, inv, L):
    W, H = A4
    m = 22 * mm
    lang = inv["language"]
    serif, serif_b, serif_i = "Times-Roman", "Times-Bold", "Times-Italic"
    grey = colors.Color(0.35, 0.35, 0.35)
    d = lambda dt: fmt_date(dt, lang, "dash")
    cx = W / 2

    y = H - m
    c.setFillColor(colors.black)
    size = fit_size(inv["supplier_name"], serif_b, 22, W - 2 * m)
    c.setFont(serif_b, size)
    c.drawCentredString(cx, y, inv["supplier_name"])
    c.setFont(serif, 9.5)
    for line in [f'{inv["address"]} - {inv["city"]}', f'{L["tel"]} : {inv["phone"]}']:
        y -= 13
        c.drawCentredString(cx, y, line)
    y -= 10
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.6)
    c.line(m, y, W - m, y)

    y -= 30
    c.setFont(serif_i, 26)
    c.drawCentredString(cx, y, L["title"])

    y -= 26
    col_w = (W - 2 * m) / 3
    cols = [(L["number"], inv["invoice_number"]), (L["date"], d(inv["date"])), (L["due"], d(inv["due_date"]))]
    for k, (lab, val) in enumerate(cols):
        ccx = m + col_w * (k + 0.5)
        c.setFillColor(grey)
        c.setFont(serif, 8.5)
        c.drawCentredString(ccx, y, lab.upper())
        c.setFillColor(colors.black)
        c.setFont(serif_b, 11.5)
        c.drawCentredString(ccx, y - 15, val)
        if k:
            c.setLineWidth(0.4)
            c.line(m + col_w * k, y + 8, m + col_w * k, y - 20)
    y -= 34
    c.setLineWidth(0.6)
    c.line(m, y, W - m, y)

    y -= 20
    c.setFont(serif_i, 10)
    c.drawString(m, y, L["client"])
    c.setFont(serif_b, 11.5)
    c.drawString(m, y - 14, inv["client_name"])
    c.setFont(serif, 10)
    c.drawString(m, y - 27, inv["client_address"])
    c.drawString(m, y - 40, f'ICE : {inv["client_ice"]}')

    widths = [w * mm for w in (80, 16, 34, 36)]
    t_bottom = draw_table(c, inv, L, m, y - 40 - 12 * mm, widths, font=serif, bold=serif_b, size=10,
                          row_h=8 * mm, head_fill=None, head_text=colors.black, grid="h")
    draw_totals(c, inv, L, W - m, t_bottom - 8 * mm, 84 * mm, font=serif, bold=serif_b, size=10,
                row_h=8 * mm, style="lines")

    c.setFillColor(grey)
    c.setFont(serif, 8.5)
    fy = 30 * mm
    for line in [f'ICE : {inv["ice"]}   -   RC : {inv["rc"]}   -   IF : {inv["if_no"]}',
                 f'{L["rib"]} : {inv["rib"]}', L["pay"]]:
        c.drawCentredString(cx, fy, line)
        fy -= 11


TEMPLATES = {"A": template_a, "B": template_b, "C": template_c}


def render_pdf(inv, path):
    L = LABELS[inv["language"]]
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle(f'{L["title"]} - {inv["supplier_name"]}')
    c.setAuthor(inv["supplier_name"])
    c.setSubject("Synthetic test invoice (fictional data)")
    TEMPLATES[inv["template"]](c, inv, L)
    c.showPage()
    c.save()


# --------------------------------------------------------------------------
def main():
    rng = random.Random(SEED)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    GT_PATH.parent.mkdir(parents=True, exist_ok=True)
    for old in OUT_DIR.glob("invoice_*.pdf"):
        old.unlink()

    expected_fault_fields = {"ttc_mismatch": {"totals"}, "ice_14_digits": {"ice"},
                             "missing_invoice_number": {"invoice_number"}}
    ground_truth = {}
    for i, sup in enumerate(SUPPLIERS):
        inv = build_invoice(i, sup, rng)

        # Self-check: exactly the intended fault (and nothing else) is present.
        errs = find_errors(inv)
        want = expected_fault_fields.get(FAULTS.get(i), set())
        assert errs == want, f"invoice {i + 1}: found {errs}, wanted {want}"

        filename = f"invoice_{i + 1:02d}_{slugify(inv['supplier_name'])}.pdf"
        render_pdf(inv, OUT_DIR / filename)
        ground_truth[filename] = to_ground_truth(inv)
        flag = f"  <-- {FAULTS[i]}" if i in FAULTS else ""
        print(f"[{inv['template']}/{inv['language']}] {filename}{flag}")

    with open(GT_PATH, "w", encoding="utf-8") as f:
        json.dump(ground_truth, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {len(ground_truth)} PDFs to {OUT_DIR}/ and ground truth to {GT_PATH}")


if __name__ == "__main__":
    main()
