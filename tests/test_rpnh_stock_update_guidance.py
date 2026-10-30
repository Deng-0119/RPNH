"""Pure guidance regression; synthetic offers do not qualify a release."""
from cpn.rpnh.updates import Offer, manual_guidance


def test_stock_guidance_does_not_offer_managed_install():
    offer = Offer("dsh-operator", "v0.1.0rc3", "0.1.0rc3", "0.2.2-alpha.2",
                  "example", "prerelease", "unused", "unused")
    text = manual_guidance(offer)
    assert "rpnh update install" not in text
    assert "matching pair" in text and "do not update" in text
    assert "https://github.com/Deng-0119/RPNH/releases/tag/v0.1.0rc3" in text
    assert "does not install packages" in text
