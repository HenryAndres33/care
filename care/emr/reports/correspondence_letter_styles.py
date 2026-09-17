LETTER_CSS = """
@page { size: A4; margin: 2.4cm 1.8cm 1.8cm; }
* { box-sizing: border-box; }
body { color: #172033; font-family: Arial, sans-serif; font-size: 10pt; line-height: 1.42; margin: 0; }
.letterhead { align-items: flex-start; border-bottom: 3px solid #18ae4b; display: flex; gap: 24px; justify-content: space-between; padding-bottom: 14px; }
.letterhead-brand { flex: 0 0 128px; min-height: 72px; }
.letterhead-logo { display: block; height: auto; max-height: 90px; max-width: 128px; }
.letterhead-identity { flex: 1 1 auto; text-align: right; }
.facility-name { color: #172033; font-size: 15pt; font-weight: 700; overflow-wrap: anywhere; }
.specialty-name { color: #18ae4b; font-size: 11pt; font-weight: 700; letter-spacing: .4px; margin-top: 3px; overflow-wrap: anywhere; text-transform: uppercase; }
.facility-contact { color: #475569; font-size: 8.5pt; line-height: 1.45; margin-top: 7px; }
.recipient-block { border-left: 3px solid #18ae4b; margin: 20px 0 14px; min-height: 68px; padding: 9px 12px; width: 56%; }
.letter-meta { background: #f3fbf5; border-bottom: 1px solid #b7dfc2; border-top: 1px solid #b7dfc2; display: grid; gap: 7px 22px; grid-template-columns: 1fr 1fr; padding: 12px 10px; }
.letter-meta div { display: grid; gap: 8px; grid-template-columns: 142px minmax(0, 1fr); min-width: 0; }
.letter-meta strong { min-width: 0; overflow-wrap: anywhere; }
.letter-meta span, .subject span { color: #64748b; font-size: 8.5pt; font-weight: 700; letter-spacing: .3px; text-transform: uppercase; }
.letter-meta span { white-space: nowrap; }
.subject { border-bottom: 1px solid #d8e5e7; font-weight: 700; margin-top: 14px; padding-bottom: 7px; }
.subject span { display: inline-block; margin-right: 16px; }
.letter-body { margin-top: 18px; min-height: 180px; orphans: 3; white-space: normal; widows: 3; }
.letter-body p { margin: 0 0 12px; }
.clinical-section { margin: 0 0 16px; page-break-inside: avoid; }
.clinical-section h2 { break-after: avoid; color: #172033; font-size: 10pt; font-weight: 700; margin: 0 0 5px; }
.clinical-section p { break-inside: avoid; margin: 0 0 7px; }
.history-list { margin: 0; padding-left: 18px; }
.history-list li { margin: 0 0 7px; padding-left: 2px; }
.history-diagnosis { font-weight: 600; }
.history-detail { margin-top: 2px; padding-left: 10px; }
.signature { margin-top: 22px; page-break-inside: avoid; }
.signature strong { display: block; margin-top: 14px; }
footer { border-top: 1px solid #b7dfc2; color: #64748b; display: flex; font-size: 7.5pt; justify-content: space-between; margin-top: 24px; padding-top: 7px; }
.controlled-correction-copy { background: #fff7ed; border: 2px solid #c2410c; margin-bottom: 18px; padding: 12px; }
.controlled-correction-copy h1 { color: #9a3412; font-size: 13pt; margin: 0 0 5px; }
.controlled-correction-copy table { border-collapse: collapse; font-size: 8pt; width: 100%; }
.controlled-correction-copy th { text-align: left; width: 30%; }
"""
