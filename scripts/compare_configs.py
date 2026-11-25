"""So sánh từng cặp cấu hình bằng bootstrap ghép cặp (paired bootstrap) trên cùng tập kiểm tra.

    python scripts/compare_configs.py --dir results/A_en
    python scripts/compare_configs.py --dir results/A_vi --pairs KB2:KB1,KB3:KB2,KB4:KB3,KB4:KB1 --n 1000
    python scripts/compare_configs.py --dir results/B_sach --group B
    python scripts/compare_configs.py --dir results/B_nhieu --group B      # tách theo mức nhiễu

Nhóm B (--group B): đọc <dir>/chi_tiet_B*.json của eval_extractor.py, lấy mẫu có hoàn lại theo CV (cùng CV cho hai
cấu hình), tính lại F1 macro (vi mô theo nhóm trường rồi trung bình bốn nhóm, như ket_qua.csv) và tỷ lệ chọn đúng
nhánh; báo cáo riêng từng mức nhiễu. Cặp mặc định B1:B0, B2:B1, B3:B2, B3:B0.

Nhóm A:
Đọc <dir>/diem_tung_cap.csv và ngưỡng của từng cấu hình trong <dir>/ket_qua.json (do eval_matching.py ghi). Mỗi vòng lấy
mẫu có hoàn lại các cặp kiểm tra, tính F1 (tại ngưỡng đã chọn trên tập kiểm định), AP và Spearman cho hai cấu hình trên
cùng mẫu; chênh lệch Δ = cấu hình sau − cấu hình trước. Báo cáo Δ trên toàn tập, khoảng tin cậy 95% (phân vị 2,5–97,5)
và p-value hai phía (tỷ lệ vòng có Δ đổi dấu). Δ có khoảng tin cậy không chứa 0 thì thành phần được thêm có đóng góp
có ý nghĩa thống kê. Kết quả: <dir>/so_sanh.csv.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import average_precision_score, f1_score


def stats(s: np.ndarray, y: np.ndarray, score: np.ndarray, thr: float) -> dict[str, float]:
    return {"f1": f1_score(y, (s >= thr).astype(int)), "ap": average_precision_score(y, s),
            "spearman": spearmanr(s, score).correlation}


def ci(arr: list[float]) -> str:
    return f"[{np.percentile(arr, 2.5):+.4f}; {np.percentile(arr, 97.5):+.4f}]"


def p_two(arr: list[float]) -> float:
    a = np.asarray(arr)
    return min(1.0, 2 * min((a <= 0).mean(), (a >= 0).mean()))


def f1_macro(items: list[dict]) -> float:
    f1s = []
    for g in ("lien_he", "hoc_van", "kinh_nghiem", "ky_nang"):
        tp, fp, fn = (sum(it["dem"][g][k] for it in items) for k in range(3))
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * p * r / (p + r) if p + r else 0.0)
    return sum(f1s) / len(f1s)


def compare_b(d: Path, pairs: str, n: int, seed: int) -> list[dict]:
    det = {f.stem.removeprefix("chi_tiet_"): {it["tep"]: it for it in json.loads(f.read_text(encoding="utf-8"))}
           for f in sorted(d.glob("chi_tiet_B*.json"))}
    rng = np.random.default_rng(seed)
    rows = []
    for pair in pairs.split(","):
        a, b = pair.split(":")
        if a not in det or b not in det:
            continue
        common = [t for t in det[a] if t in det[b]]
        levels = sorted({det[a][t]["muc"] for t in common})
        for lv in (levels if len(levels) == 1 else ["tat_ca", *levels]):
            tep = [t for t in common if lv == "tat_ca" or det[a][t]["muc"] == lv]
            ia, ib = [det[a][t] for t in tep], [det[b][t] for t in tep]
            nhanh = lambda xs: sum(x["dung_nhanh"] for x in xs) / len(xs)  # noqa: E731
            full = {"f1_macro": f1_macro(ia) - f1_macro(ib), "chon_dung_nhanh": nhanh(ia) - nhanh(ib)}
            boot = {k: [] for k in full}
            for _ in range(n):
                i = rng.integers(0, len(tep), len(tep))
                sa, sb = [ia[j] for j in i], [ib[j] for j in i]
                boot["f1_macro"].append(f1_macro(sa) - f1_macro(sb))
                boot["chon_dung_nhanh"].append(nhanh(sa) - nhanh(sb))
            row = {"so_sanh": f"{a} so với {b}", "muc_nhieu": lv, "so_cv": len(tep)}
            for k, v in full.items():
                row[f"delta_{k}"], row[f"ktc95_{k}"], row[f"p_{k}"] = v, ci(boot[k]), p_two(boot[k])
            rows.append(row)
            print(row)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--group", choices=["A", "B"], default="A")
    ap.add_argument("--pairs", help="mặc định KB2:KB1,KB3:KB2,KB4:KB3,KB4:KB1 (A) hoặc B1:B0,B2:B1,B3:B2,B3:B0 (B)")
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    d = Path(args.dir)
    if args.group == "B":
        rows = compare_b(d, args.pairs or "B1:B0,B2:B1,B3:B2,B3:B0", args.n, args.seed)
        pd.DataFrame(rows).to_csv(d / "so_sanh.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
        print(f"Đã lưu {d / 'so_sanh.csv'}")
        return
    args.pairs = args.pairs or "KB2:KB1,KB3:KB2,KB4:KB3,KB4:KB1"
    df = pd.read_csv(d / "diem_tung_cap.csv")
    thr = {r["cau_hinh"]: r["nguong"] for r in json.loads((d / "ket_qua.json").read_text(encoding="utf-8"))["ket_qua"]}
    score = df.final_score.to_numpy()
    y = (score > 0).astype(int)
    rng = np.random.default_rng(args.seed)
    idx = [rng.integers(0, len(df), len(df)) for _ in range(args.n)]

    rows = []
    for pair in args.pairs.split(","):
        a, b = pair.split(":")
        if a not in df or b not in df:
            continue
        sa, sb = df[a].to_numpy(), df[b].to_numpy()
        full = {k: stats(sa, y, score, thr[a])[k] - stats(sb, y, score, thr[b])[k] for k in ("f1", "ap", "spearman")}
        boot = {k: [] for k in full}
        for i in idx:
            ma, mb = stats(sa[i], y[i], score[i], thr[a]), stats(sb[i], y[i], score[i], thr[b])
            for k in full:
                boot[k].append(ma[k] - mb[k])
        row = {"so_sanh": f"{a} so với {b}"}
        for k, v in full.items():
            arr = np.asarray(boot[k])
            row[f"delta_{k}"] = v
            row[f"ktc95_{k}"] = ci(arr)
            row[f"p_{k}"] = p_two(arr)
        rows.append(row)
        print(row)
    pd.DataFrame(rows).to_csv(d / "so_sanh.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    print(f"Đã lưu {d / 'so_sanh.csv'}")


if __name__ == "__main__":
    main()
