"""Email_Agent: gửi thư mời phỏng vấn / từ chối từ kết quả của run_pipeline.py.

    python scripts/send_emails.py data/processed --vi-tri "Backend Developer" [--send]

Đọc các cặp <tên>.profile.json + <tên>.report.json. Mặc định chỉ lưu thư nháp .eml vào outbox để kiểm tra;
thêm --send để gửi thật (cần biến môi trường SMTP_USER, SMTP_PASSWORD).
"""
from __future__ import annotations

import argparse
from pathlib import Path

from cvagents.common.schemas import AnalysisReport, CVProfile
from cvagents.mailer import EmailAgent, SmtpSettings, decide


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", help="thư mục chứa *.profile.json và *.report.json")
    ap.add_argument("--vi-tri", required=True, help="tên vị trí tuyển dụng ghi trong thư")
    ap.add_argument("--send", action="store_true", help="gửi thật qua SMTP; mặc định chỉ lưu .eml")
    args = ap.parse_args()

    smtp = SmtpSettings.from_env()
    if args.send and smtp is None:
        raise SystemExit("Chưa đặt SMTP_USER / SMTP_PASSWORD nên không gửi được.")
    agent = EmailAgent(smtp=smtp)

    drafts = []
    for rp in sorted(Path(args.folder).glob("*.report.json")):
        pp = rp.with_name(rp.name.replace(".report.json", ".profile.json"))
        if not pp.exists():
            print(f"Bỏ qua {rp.name}: thiếu {pp.name}")
            continue
        report = AnalysisReport.model_validate_json(rp.read_text(encoding="utf-8"))
        profile = CVProfile.model_validate_json(pp.read_text(encoding="utf-8"))
        to = profile.thong_tin_ca_nhan.email
        if not to:
            print(f"Bỏ qua {rp.name}: CV không có email")
            continue
        drafts.append(agent.compose(to, profile.thong_tin_ca_nhan.ho_ten, args.vi_tri, decide(report)))

    for r in agent.send(drafts, dry_run=not args.send):
        print(f"{r.status:6} {r.to}  {r.detail}")


if __name__ == "__main__":
    main()
