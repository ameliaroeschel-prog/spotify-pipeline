"""Import Amelia's hand-labeled golden 50 from Excel (stdlib only) into evals/golden_labels.csv.

Only formatting is normalized: severity floats -> int, 0/1 -> booleans, and evidence quotes are
matched to the exact source span when they differ only by case or surrounding spaces. Every such
fix is recorded in the quote_fix column. Labels themselves are never changed.

    python3 -m pipeline.golden_import "~/Downloads/Golden 50 Labeled (2).xlsx"
"""

import csv
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from pipeline.config import INTENTS, ROOT, TOPICS

M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
FIELDS = ["topic", "intent", "sentiment", "severity", "entities", "evidence_quote", "needs_review"]


def read_xlsx(path):
    z = zipfile.ZipFile(path)
    shared = [''.join(t.text or '' for t in si.iter(M + 't'))
              for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall(M + 'si')]
    rows = []
    for r in ET.fromstring(z.read('xl/worksheets/sheet1.xml')).find(M + 'sheetData'):
        row = {}
        for c in r:
            col = re.match(r'[A-Z]+', c.get('r')).group()
            v, t = c.find(M + 'v'), c.get('t')
            if t == 's' and v is not None:
                row[col] = shared[int(v.text)]
            elif t == 'inlineStr':
                row[col] = ''.join(x.text or '' for x in c.iter(M + 't'))
            else:
                row[col] = v.text if v is not None else ''
        rows.append(row)
    header = rows[0]
    return [{header[k]: v for k, v in r.items() if k in header} for r in rows[1:] if r.get('A', '').strip()]


def exact_quote(text, quote):
    q = (quote or '').strip()
    if not q:
        return None, "blank"
    if quote in text:
        return quote, ""
    if q in text:
        return q, "trimmed_spaces"
    i = text.lower().find(q.lower())
    if i >= 0:
        return text[i:i + len(q)], "matched_case"
    return None, "not_found"


def main():
    xlsx = Path(sys.argv[1]).expanduser()
    source = {r['review_id']: r for r in csv.DictReader(open(ROOT / 'data/golden_50_to_label.csv', encoding='utf-8'))}
    labeled = read_xlsx(xlsx)
    problems, out = [], []
    for r in labeled:
        rid = r['review_id']
        if rid not in source:
            problems.append(f"{rid}: not in golden source")
            continue
        text = source[rid]['review_text']           # always the original text, never the spreadsheet copy
        quote, fix = exact_quote(text, r.get('evidence_quote'))
        sev = int(float(r['severity'])) if r.get('severity', '').strip() else None
        sent = float(r['sentiment']) if r.get('sentiment', '').strip() else None
        nr = str(r.get('needs_review', '')).strip().upper() in ('1', 'TRUE', '1.0')
        if r.get('topic') not in TOPICS: problems.append(f"{rid}: topic {r.get('topic')!r}")
        if r.get('intent') not in INTENTS: problems.append(f"{rid}: intent {r.get('intent')!r}")
        if sev not in (1, 2, 3, 4, 5): problems.append(f"{rid}: severity {r.get('severity')!r}")
        if sent is None or not -1 <= sent <= 1: problems.append(f"{rid}: sentiment {r.get('sentiment')!r}")
        if quote is None: problems.append(f"{rid}: quote {fix} ({(r.get('evidence_quote') or '')[:40]!r})")
        out.append({**{k: source[rid][k] for k in source[rid] if k not in FIELDS},
                    "topic": r.get('topic'), "intent": r.get('intent'), "sentiment": sent, "severity": sev,
                    "entities": ";".join(e.strip().lower() for e in re.split(r"[;,]", r.get('entities') or '') if e.strip()),
                    "evidence_quote": quote or '', "needs_review": nr, "label_source": "amelia_hand_labeled",
                    "quote_fix": fix})
    missing = set(source) - {o['review_id'] for o in out}
    problems += [f"{m}: missing from spreadsheet" for m in missing]
    dest = ROOT / 'evals/golden_labels.csv'
    dest.parent.mkdir(exist_ok=True)
    with open(dest, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    print(f"imported {len(out)} rows -> {dest.relative_to(ROOT)}; quote fixes: "
          f"{sum(1 for o in out if o['quote_fix'])}; problems: {len(problems)}")
    for p in problems:
        print("  PROBLEM", p)


if __name__ == "__main__":
    main()
