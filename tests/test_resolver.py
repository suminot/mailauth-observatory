

def test_DNS_の名前として書けないものは問い合わせずに引けなかったと返す():
    """米国 2026-11-07 の P3 が `dns.name.BadEscape` 1件で止まった。"""
    from mailauth.resolver import DnsResolver

    r = DnsResolver(nameservers=["192.0.2.1"], retries=0, timeout=0.01)
    answer = r.query("www.bad\\999name.com", "MX")
    assert answer.observed is False
    assert answer.record_present is None
    assert answer.rcode == "BADNAME"


def test_ホスト名として正しくないものは登録ドメインにしない():
    from mailauth.normalize import etld_plus_one, is_valid_hostname

    assert is_valid_hostname("www.example.co.jp")
    assert is_valid_hostname("_dmarc.example.com")
    assert is_valid_hostname("例え.jp")
    assert not is_valid_hostname("www.bad\\999name.com")
    assert not is_valid_hostname("www.exa mple.com")
    assert not is_valid_hostname("www.-broken.com")
    assert not is_valid_hostname("a" * 64 + ".com")
    assert etld_plus_one("www.bad\\999name.com") is None
    assert etld_plus_one("shop.example.co.jp") == "example.co.jp"
