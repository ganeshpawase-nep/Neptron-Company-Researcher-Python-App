import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from company.researcher import Researcher

class Dummy:
    pass

def test_rejects_location_as_sector():
    r = Researcher(Dummy(), Dummy())
    assert not r._valid_classification("65 Noida-U", "sector")
    assert not r._valid_classification("N/A", "sector")

def test_accepts_tema_industry_sector():
    r = Researcher(Dummy(), Dummy())
    assert r._valid_classification("Process Equipment", "industry")
    assert r._valid_classification(
        "Heat Exchangers, Oil & Gas, Petrochemicals, Power Generation",
        "sector",
    )

if __name__ == "__main__":
    test_rejects_location_as_sector()
    test_accepts_tema_industry_sector()
    print("Profile parsing tests passed.")


def test_expanded_search_assist_extracts_exact_labeled_fields():
    r = Researcher(Dummy(), Dummy())
    text = """Search Assist
Company Profile: Telawne Power Equipments Pvt. Ltd.
Industry and Sector
Industry: Transformer Manufacturing
Sector: Electrical Equipment
Company Information
Founded: 1984
Employees Count: 51-200
Address: A-129, Thane, Maharashtra, India
Contact Number: +91-1234567890
Website: https://example.com
LinkedIn: https://www.linkedin.com/company/example
"""
    fields = r._parse_search_assist_profile(text)
    assert fields["industry"] == "Transformer Manufacturing"
    assert fields["sector"] == "Electrical Equipment"
    assert fields["year"] == "1984"
    assert fields["employees"] == "51-200"
    assert fields["address"] == "A-129, Thane, Maharashtra, India"
    assert fields["phone"] == "+91-1234567890"
    assert fields["website"] == "https://example.com"
    assert fields["linkedin"] == "https://www.linkedin.com/company/example"


def test_summary_sentence_is_not_used_as_sector():
    r = Researcher(Dummy(), Dummy())
    text = (
        "Telawne Power Equipments Pvt. Ltd. operates in the transformer "
        "manufacturing industry under the electrical equipment sector."
    )
    fields = r._parse_search_assist_profile(text)
    assert fields["industry"] == ""
    assert fields["sector"] == ""


def test_inline_labeled_profile_fields():
    r = Researcher(Dummy(), Dummy())
    text = (
        "Industry: Process Equipment Sector: Heat Exchangers, Oil & Gas, "
        "Petrochemicals Founded: 1984 Employees: 51-200"
    )
    fields = r._parse_search_assist_profile(text)
    assert fields["industry"] == "Process Equipment"
    assert fields["sector"] == "Heat Exchangers, Oil & Gas, Petrochemicals"
    assert fields["year"] == "1984"
    assert fields["employees"] == "51-200"
