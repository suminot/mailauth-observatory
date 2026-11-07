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
        "株式会社甲乙丙ロジスティクス", "甲乙丙テクノ株式会社",
    ]


def test_節が無ければ空():
    assert subsidiary_names("有価証券報告書 本文") == []


def test_一覧は会社ごとに書き戻せる(tmp_path):
    path = tmp_path / "subs.csv"
    data = {"E00001": ("S100", ["株式会社A工業", "B商事株式会社"], "2026-11-07"),
            "E00002": ("S200", [], "2026-11-07")}
    write_csv(path, data)
    assert read_csv(path) == data


def test_地の文にくっついた名前を切り離す():
    text = (
        "4【関係会社の状況】 （連結子会社） 株式会社ココホテルズ及び株式会社バリュー 東京都 "
        "（注）前連結会計年度において東京ガス株式会社 の株式を売却。"
        "株式会社箱根ときのみを100%出資により設立しております。及び株式会社 "
        "5【従業員の状況】"
    )
    assert subsidiary_names(text) == [
        "株式会社ココホテルズ", "株式会社バリュー", "東京ガス株式会社", "株式会社箱根ときのみ",
    ]


def test_子会社の区分だけ読む():
    names = subsidiary_names(TEXT)
    assert "合同会社甲乙丙エナジー" not in names  # 持分法の関連会社
    assert "株式会社甲乙丙ホールディングス" not in names  # 親会社
    assert names == ["株式会社甲乙丙ロジスティクス", "甲乙丙テクノ株式会社"]


def test_読み方の版が変わったら読んだ有報もキャッシュから読み直す(tmp_path):
    import datetime as dt
    import json

    from mailauth.p1_population import edinet_subsidiaries as subs

    class Reader:
        cache_dir = tmp_path
        today = dt.date(2026, 11, 8)
        fetched = 0

        def latest_yuho(self, codes):
            return {"E00001": "S100", "E00002": "S200"}

        def api_get(self, *a, **k):
            raise AssertionError("キャッシュがあるので取りに行かない")

    (tmp_path / "subs").mkdir()
    for doc in ("S100", "S200"):
        (tmp_path / "subs" / f"{doc}.json").write_text(json.dumps(
            {"version": subs.SUBS_CACHE_VERSION, "section": TEXT}), encoding="utf-8")
    old = {"E00001": ("S100", ["及び株式会社"], "2026-11-07")}
    out, stats = subs.scan(Reader(), [("E00001", None), ("E00002", None)], old,
                           max_docs=0, parsed_with=None)
    assert out["E00001"][1] == ["株式会社甲乙丙ロジスティクス", "甲乙丙テクノ株式会社"]
    assert stats["reparsed"] == 2 and stats["read"] == 0
    # 版が同じなら読み直さない
    out, stats = subs.scan(Reader(), [("E00001", None)], old, max_docs=0)
    assert out["E00001"][1] == ["及び株式会社"] and stats["already"] == 1


def test_一覧の版は列から読む(tmp_path):
    from mailauth.p1_population import edinet_subsidiaries as subs

    path = tmp_path / "subs.csv"
    path.write_text("edinet_code,doc_id,subsidiary,scanned_on\nE1,S1,株式会社A工業,2026-11-07\n",
                    encoding="utf-8")
    assert subs.csv_parser_version(path) is None
    write_csv(path, read_csv(path))
    assert subs.csv_parser_version(path) == subs.SUBS_PARSER_VERSION


def test_住所と助詞と決算日の地の文を切り離す():
    text = (
        "4【関係会社の状況】 （連結子会社） "
        "東京都千代田区丸の内一丁目4番5号三菱UFJ信託銀行株式会社 "
        "株式会社日本カストディ銀行は、株式会社アスコットの決算日を9月30日から変更 "
        "5【従業員の状況】"
    )
    assert subsidiary_names(text) == [
        "三菱UFJ信託銀行株式会社", "株式会社日本カストディ銀行", "株式会社アスコット",
    ]


def test_表の住所の欄から都道府県を読む():
    from mailauth.p1_population.edinet_subsidiaries import places_from_section, prefecture_of

    names = subsidiary_names(TEXT)
    assert places_from_section(TEXT, names) == {
        "株式会社甲乙丙ロジスティクス": "東京都", "甲乙丙テクノ株式会社": "大阪府",
    }
    assert prefecture_of("横浜市西区") == "神奈川県"
    assert prefecture_of("東京都港区") == "東京都"
    assert prefecture_of("米国") is None


def test_一覧に都道府県の列を書いて読める(tmp_path):
    from mailauth.p1_population.edinet_subsidiaries import read_places

    path = tmp_path / "subs.csv"
    data = {"E1": ("S1", ["株式会社A工業", "B商事株式会社"], "2026-11-08")}
    write_csv(path, data, {("E1", "株式会社A工業"): "愛知県"})
    assert read_csv(path) == data
    assert read_places(path) == {("E1", "株式会社A工業"): "愛知県"}
