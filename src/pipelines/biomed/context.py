"""What a biomedical paper's PubMed record says about every claim in it: study type (publication types), species
(MeSH tags) and date, and which sections report the paper's own findings (primary). The rules are the [context]
settings in defaults.toml."""


def study_type(publication_types: list[str], settings: dict) -> str:
    for kind, patterns in settings["study_types"].items():
        if any(t.startswith(p[:-1]) if p.endswith("*") else t == p for p in patterns for t in publication_types):
            return kind
    return settings["default_study_type"]


def paper(record: dict, settings: dict) -> dict:
    """The fields every claim from this paper carries."""
    return {"date": record.get("date"), "study_type": study_type(record.get("publication_types") or [], settings),
            "species": sorted({settings["species"][m] for m in record.get("mesh") or [] if m in settings["species"]})}


def primary(section: str, label: str | None, settings: dict) -> bool:
    return section in settings["primary_sections"] or (
        section == "abstract" and (label or "") in settings["primary_abstract_labels"])
