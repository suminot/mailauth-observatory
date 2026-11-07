"""有報の「関係会社の状況」から子会社名を読む（BACKLOG 52）。ネットワークに出ない。"""

from __future__ import annotations

from mailauth.p1_population.edinet_subsidiaries import read_csv, subsidiary_names, write_csv

TEXT = (
    "目次 4【関係会社の状況】 5【従業員の状況】 ... "
    "4【関係会社の状況】 名称 住所 資本金 主要な事業の内容 議決権の所有割合 "
    "（連結子会社） 株式会社甲乙丙ロジスティクス 東京都港区 100 物流 100.0 "
    "甲乙丙テクノ株式会社 ※1 大阪市 50 製造 80.0 "
    "Kouotsuhei America, Inc. 米国 1,000 販売 100.0 "
    "（持分法適用関連会社） 合同会社甲乙丙エナジー 東京都 10 発電 40.0 "
    "（注）1．株式会社甲乙丙ロジスティクス は特定子会社です。 "
    "（親会社） 株式会社甲乙丙ホールディングス "
    "5【従業員の状況】 (1) 連結会社の状況 株式会社関係ない会社"
)


def test_関係会社の状況の節から国内の法人格付きの名前を出てきた順に取る():
    names = subsidiary_names(TEXT, own_name="株式会社甲乙丙ホールディングス")
    assert names == [
        "株式会社甲乙丙ロジスティクス", "甲乙丙テクノ株式会社", "合同会社甲乙丙エナジー",
    ]


def test_節が無ければ空():
    assert subsidiary_names("有価証券報告書 本文") == []


def test_一覧は会社ごとに書き戻せる(tmp_path):
    path = tmp_path / "subs.csv"
    data = {"E00001": ("S100", ["株式会社A工業", "B商事株式会社"], "2026-11-07"),
            "E00002": ("S200", [], "2026-11-07")}
    write_csv(path, data)
    assert read_csv(path) == data
