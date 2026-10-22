"""Customer PII is replaced before any model sees it. All numbers below are synthetic:
the ID numbers are constructed to pass the checksum, the card is a standard test number."""

from __future__ import annotations

from frontdesk.redact import MAX_MODEL_INPUT_CHARS, is_sa_id, pii_leaks, redact

SYNTHETIC_ID = "8501015800088"  # 1985-01-01, valid Luhn
TEST_CARD = "4111 1111 1111 1111"


def test_a_valid_sa_id_number_is_replaced_and_its_value_is_not_kept():
    r = redact(f"Hi, my ID is {SYNTHETIC_ID}, please check my warranty")
    assert SYNTHETIC_ID not in r.text
    assert "[ID_NUMBER]" in r.text
    assert r.kinds == ["ID_NUMBER"]
    assert SYNTHETIC_ID not in str(r.as_record())


def test_an_id_number_with_spaces_is_still_caught():
    assert "[ID_NUMBER]" in redact("ID 850101 5800 08 8 thanks").text


def test_thirteen_digits_that_fail_the_checksum_are_not_called_an_id():
    assert not is_sa_id("8501015800085")


def test_card_numbers_are_replaced():
    r = redact(f"Can I pay with {TEST_CARD}?")
    assert "[CARD_NUMBER]" in r.text and "4111" not in r.text


def test_phone_numbers_in_local_and_international_format_are_replaced():
    for phone in ("082 555 0101", "+27 82 555 0101", "0825550101", "27825550101"):
        assert "[PHONE]" in redact(f"call me on {phone}").text, phone


def test_emails_bank_accounts_and_street_addresses_are_replaced():
    r = redact("Email lerato@example.com, account no 62012345678, I stay at 12 Marshall Street in Seshego")
    assert "[EMAIL]" in r.text and "[BANK_ACCOUNT]" in r.text and "[STREET_ADDRESS]" in r.text
    assert "Seshego" in r.text  # the suburb decides the travel zone, so it stays


def test_prices_power_ratings_and_order_numbers_are_left_alone():
    text = "How much is the 5kW package, R89 500? My order is BSE-1042, quote 20261014."
    assert redact(text).text == text


def test_long_messages_are_truncated_before_the_model():
    r = redact("a" * (MAX_MODEL_INPUT_CHARS + 500))
    assert r.truncated and len(r.text) == MAX_MODEL_INPUT_CHARS


def test_the_leak_detector_finds_what_redaction_would_have_removed():
    assert pii_leaks(f"id {SYNTHETIC_ID}") == ["ID_NUMBER"]
    assert pii_leaks(redact(f"id {SYNTHETIC_ID} phone 082 555 0101").text) == []
