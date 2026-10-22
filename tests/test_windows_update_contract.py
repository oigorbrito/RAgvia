from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_update_verifier_fails_closed_on_signature_and_hash() -> None:
    text = (ROOT / "scripts/verify_windows_update.ps1").read_text(encoding="utf-8-sig")
    assert "Get-AuthenticodeSignature" in text
    assert "Status -ne 'Valid'" in text
    assert "Get-FileHash" in text
    assert "hash do instalador não corresponde" in text
    assert "version" in text and "publisher" in text
    assert "Atualização rejeitada" in text


def test_update_verifier_does_not_apply_or_touch_docker() -> None:
    text = (ROOT / "scripts/verify_windows_update.ps1").read_text(encoding="utf-8-sig")
    assert "Start-Process" not in text
    assert "docker" not in text.lower()
    assert "Copy-Item" not in text
    assert "Remove-Item" not in text


def test_inno_installer_is_the_current_artifact_boundary() -> None:
    text = (ROOT / "packaging/windows/RagVDesktop.iss").read_text(encoding="utf-8-sig")
    assert "OutputBaseFilename" in text
    assert "RagVDesktop.exe" in text
    assert "ProjectDir" in text
    assert "PrivilegesRequired=lowest" in text
