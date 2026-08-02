from app.services.credentials import normalize_oauth_credential_fields


def test_swap_client_id_and_refresh():
    out = normalize_oauth_credential_fields(
        {
            "client_id": "M.C523_SN1.0.U.MsaArtifacts.xxx_long_" + "x" * 200,
            "refresh_token": "9e5f94bc-e8a4-4e73-b8be-63364c29d753",
        }
    )
    assert out["client_id"] == "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
    assert out["refresh_token"].startswith("M.")


def test_keep_correct_order():
    out = normalize_oauth_credential_fields(
        {
            "client_id": "9e5f94bc-e8a4-4e73-b8be-63364c29d753",
            "refresh_token": "M.C523_" + "y" * 200,
        }
    )
    assert out["client_id"].startswith("9e5f")
    assert out["refresh_token"].startswith("M.")
