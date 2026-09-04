from app.labels.service import LabelSpaceService


def test_spacenet_14_mapping(settings):
    labels = LabelSpaceService(settings.label_space_root).get("spacenet_14")
    assert len(labels.classes) == 14
    assert labels.classes[0].name == "WiFi 20MHz QPSK"
    assert labels.classes[9].name == "LoRa 250kHz"
    assert labels.classes[13].name == "FM"
