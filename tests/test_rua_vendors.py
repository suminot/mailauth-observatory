"""DMARC rua 宛先 → レポート処理ベンダーの辞書（configs/vendors/dmarc_rua_vendors.yaml）。

足したのは、2026-10 の米国計測で多かった宛先のうち、**ベンダー自身の資料に
rua の例が載っているもの**だけ。資料で確かめられなかった宛先は同定しない。
"""

from __future__ import annotations

import pytest
import yaml

from mailauth.p2_candidates.runner import load_report_vendor_patterns, match_report_vendor

PATH = "configs/vendors/dmarc_rua_vendors.yaml"


@pytest.fixture(scope="module")
def patterns():
    return load_report_vendor_patterns()


@pytest.mark.parametrize(
    ("domain", "vendor"),
    [
        ("vali.email", "Valimail"),
        ("dmarcanalyzer.com", "Mimecast"),
        ("rep.dmarcanalyzer.com", "Mimecast"),
        ("dmarc-reports.cloudflare.net", "Cloudflare"),
        ("rua.easydmarc.us", "EasyDMARC"),
        ("rua.easydmarc.eu", "EasyDMARC"),
        ("rua.easydmarc.asia", "EasyDMARC"),
        ("mxtoolbox.dmarc-report.com", "MxToolbox"),
        ("inbox.ondmarc.com", "Red Sift"),
        ("dmarc.barracudanetworks.com", "Barracuda"),
        ("onsecureserver.net", "GoDaddy"),
        ("dmarc.brevo.com", "Brevo"),
        ("ag.dmarcly.com", "DMARCLY"),
        ("inbound.dmarcdigests.com", "Postmark"),
        ("dmarc.postmarkapp.com", "Postmark"),
        ("sdmarc.net", "Sendmarc"),
        ("in.mailhardener.com", "Mailhardener"),
        ("reports-sg.inkydmarc.com", "INKY"),
        ("ar.glockapps.com", "GlockApps"),
        # 2026-11-01：国内の宛先から、ベンダーの資料で確かめた
        ("mpub.ne.jp", "エンバーポイント"),
        ("dmarcrua.tmems-jp.trendmicro.com", "Trend Micro"),
        ("dmarcrua.tmes.trendmicro.eu", "Trend Micro"),
        ("emailfraudprotect.email", "Broadcom"),
    ],
)
def test_資料で確かめた宛先はベンダーに結び付く(patterns, domain, vendor):
    assert match_report_vendor(domain, patterns) == vendor


@pytest.mark.parametrize(
    "domain",
    [
        # 2026-08 の版のつづり誤り。Mimecast の資料には無い
        "dmarc-analyzer.com",
        # ベンダー自身の資料に例が見つからなかったもの
        "mimecastdmarcanalyzer.com",
        "cp-dmarc.com",
        "redsift.cloud",
        "everest.email",
        "dmarcinput.com",
        # trendmicro.com 全体には広げない
        "trendmicro.com",
        "mail.trendmicro.com",
        # 運営者は分かったが、資料に宛先のドメインが無い
        "securemx.jp",
        "dmarc.smtps.jp",
        "bmscore.jp",
        "dmarc-report.sakura.ne.jp",
        "jp.teams.ms",
        # Brevo の本体（送信元）や、似た名前の別ドメイン
        "brevo.com",
        "barracudanetworks.com",
        "notvali.email.example.com",
    ],
)
def test_確かめていない宛先は同定しない(patterns, domain):
    assert match_report_vendor(domain, patterns) is None


def test_足した規則には資料の出典がある():
    data = yaml.safe_load(open(PATH, encoding="utf-8"))
    missing = [r["id"] for r in data["rules"] if not r.get("source")]
    # 2026-08 以前の規則（出典の欄が無かった頃のもの）は除く
    legacy = {
        "rua-twofive-01",
        "rua-dmarcian-01",
        "rua-agari-01",
        "rua-proofpoint-01",
        "rua-urirportal-01",
        "rua-powerdmarc-01",
    }
    assert set(missing) <= legacy, f"出典が無い: {sorted(set(missing) - legacy)}"


def test_同定しない一覧と規則が重ならない(patterns):
    data = yaml.safe_load(open(PATH, encoding="utf-8"))
    for entry in data["unidentified_rua_domains"]:
        assert match_report_vendor(entry["domain"], patterns) is None, entry["domain"]
