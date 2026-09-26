"""Server-owned A4 correspondence design, including the AZP Flow letterhead."""

LETTER_CSS = """
@page { size: A4; margin: 2.4cm 1.6cm 1.8cm; }
* { box-sizing: border-box; }
body { color: #172d32; font-family: 'DejaVu Sans', Arial, sans-serif; font-size: 9.5pt; line-height: 1.45; margin: 0; }
.letterhead { align-items: flex-start; border-bottom: 3px solid #18ae4b; display: flex; gap: 24px; justify-content: space-between; padding-bottom: 14px; break-inside: avoid; }
.letterhead-brand { flex: 0 0 128px; min-height: 72px; }
.letterhead-logo { display: block; height: auto; max-height: 90px; max-width: 128px; }
.letterhead-identity { flex: 1 1 auto; text-align: right; }
.facility-name { color: #172d32; font-size: 15pt; font-weight: 700; overflow-wrap: anywhere; }
.specialty-name { color: #15834c; font-size: 10pt; font-weight: 700; letter-spacing: .4px; margin-top: 3px; overflow-wrap: anywhere; text-transform: uppercase; }
.facility-contact { color: #647579; font-size: 7.5pt; line-height: 1.5; margin-top: 7px; }
.flow-letterhead { background: #127b4e; border: 0; border-radius: 4mm 4mm 0 0; display: block; padding: 7mm 7mm 17mm; position: relative; }
.flow-identity { display: table; width: 100%; }
.flow-letterhead .letterhead-brand { background: #fff; border-radius: 3mm; display: table-cell; min-height: 0; padding: 3mm; vertical-align: middle; width: 27mm; }
.flow-letterhead .letterhead-logo { max-height: none; max-width: none; width: 21mm; }
.flow-letterhead .letterhead-identity { display: table-cell; padding-left: 6mm; text-align: left; vertical-align: middle; }
.flow-letterhead .facility-name { color: #fff; font-size: 12pt; }
.flow-letterhead .specialty-name { color: #d2edda; font-size: 9pt; }
.document-title { color: #fff; font-size: 22pt; line-height: 1.2; margin: 4mm 0 0; overflow-wrap: anywhere; }
.flow-letterhead .facility-contact { color: #e4f3e9; font-size: 7pt; margin-top: 4mm; position: relative; z-index: 1; }
.flow-wave { bottom: -1px; display: block; height: 15mm; left: 0; position: absolute; width: 100%; }
.recipient-block { margin: 4mm 0; padding: 0; break-inside: avoid; }
.recipient-block div:empty { display: none; }
.letter-meta { background: #eff6f1; border-radius: 3mm; display: grid; gap: 3mm 7mm; grid-template-columns: 1fr 1fr; padding: 4mm 5mm; break-inside: avoid; }
.letter-meta div { min-width: 0; }
.letter-meta strong { display: block; font-size: 9pt; overflow-wrap: anywhere; }
.letter-meta span, .subject span { color: #155c3e; font-size: 7pt; font-weight: 700; letter-spacing: .3px; text-transform: uppercase; }
.subject { font-weight: 700; margin-top: 4mm; break-inside: avoid; break-after: avoid; overflow-wrap: anywhere; }
.subject span { display: inline-block; margin-right: 12px; }
.letter-body { margin-top: 5mm; orphans: 3; widows: 3; overflow-wrap: anywhere; }
.letter-body p { margin: 0 0 3mm; }
.clinical-section { margin: 0 0 4mm; }
.clinical-section h2 { background: #edf5ef; border-radius: 1.5mm; break-after: avoid; color: #155c3e; font-size: 9.5pt; font-weight: 700; margin: 0 0 2mm; padding: 1.5mm 3mm; }
.clinical-section p { margin: 0 3mm 2mm; }
.history-list { margin: 0; padding-left: 22px; }
.history-list li { margin: 0 0 2mm; padding-left: 2px; break-inside: avoid; }
.history-diagnosis { font-weight: 600; }
.history-detail { margin-top: 2px; padding-left: 10px; }
.signature { margin-top: 5mm; break-inside: avoid; }
.signature strong { display: block; margin-top: 3mm; }
footer { background: #eff6f1; border-radius: 2mm; color: #647579; display: flex; font-size: 7pt; justify-content: space-between; margin-top: 6mm; padding: 3mm; break-inside: avoid; }
.controlled-correction-copy { background: #fff7ed; border: 2px solid #c2410c; margin-bottom: 18px; padding: 12px; break-inside: avoid; }
.controlled-correction-copy h1 { color: #9a3412; font-size: 13pt; margin: 0 0 5px; }
.controlled-correction-copy table { border-collapse: collapse; font-size: 8pt; width: 100%; }
.controlled-correction-copy th { text-align: left; width: 30%; }
"""
