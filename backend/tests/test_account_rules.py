from types import SimpleNamespace

from app.domain.account_rules import is_sold_account, is_sold_batch


def test_sold_batch_rule_is_accent_and_case_insensitive():
    assert is_sold_batch("ĐÃ BÁN")
    assert is_sold_batch("đã bán")
    assert is_sold_batch("  DA-BAN  ")
    assert is_sold_batch("SOLD")


def test_similar_operational_batches_are_not_misclassified():
    assert not is_sold_batch("đã lấy, để lại check die")
    assert not is_sold_batch("bán thử nghiệm")
    assert not is_sold_account(SimpleNamespace(batch_tag="DEFAULT"))
