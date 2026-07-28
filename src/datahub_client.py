"""DataHub client — the read + write-back surface semantic-drift-auditor needs.

The transport layer (auth, GraphQL POST, aspect GET, error wrapping) is shared with our
other submission, `lineage-guard`; the query methods below are specific to this project.

Everything here is verified against DataHub OSS Core v1.5.0.6 (GMS :8080).

Two OSS facts that shaped this file:

* There is **no glossary-term-version API** on OSS. `getGlossaryTermVersions` and
  `compareGlossaryTermVersions` do not exist in the shipped schema. What *does* exist is
  the generic aspect version chain (`/aspects/<urn>?aspect=glossaryTermInfo&version=N`,
  N=0 newest) and the Timeline API — both core, both stable. We walk those.
* The Timeline API lives at **`/openapi/v2/timeline/v1/<urn>`**. The v3 path 404s.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field


class DataHubError(RuntimeError):
    """Raised when GMS returns an error payload or a non-2xx response."""


@dataclass
class TermRevision:
    """One version of a glossary term's definition.

    ``observed_at`` (epoch ms, from systemMetadata.lastObserved) is the authoritative
    ordering key — see ``DataHubClient.term_revisions`` for why the version number is not.
    """

    version: int
    name: str
    definition: str
    observed_at: int = 0


@dataclass
class TimelineChange:
    """A DOCUMENTATION change event from the Timeline API."""

    timestamp: int
    actor: str
    operation: str
    sem_ver: str
    description: str


@dataclass
class TaggedAsset:
    """An asset the glossary term is applied to."""

    urn: str
    entity_type: str
    name: str = ""
    platform: str = ""


@dataclass
class DownstreamAsset:
    """An asset reachable downstream from a term-tagged asset."""

    urn: str
    entity_type: str
    degree: int
    name: str = ""
    platform: str = ""
    owners: list[str] = field(default_factory=list)


def _entity_type_of(urn: str) -> str:
    """`urn:li:glossaryTerm:abc` -> `glossaryterm` (the OpenAPI v3 path segment)."""
    parts = urn.split(":")
    return parts[2].lower() if len(parts) > 2 else "glossaryterm"


class DataHubClient:
    def __init__(
        self,
        gms_url: str | None = None,
        token: str | None = None,
        user: str | None = None,
        password: str | None = None,
        timeout: int = 30,
    ) -> None:
        self.gms_url = (gms_url or os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080")).rstrip("/")
        self.timeout = timeout
        token = token or os.environ.get("DATAHUB_GMS_TOKEN")
        if token:
            self._auth = f"Bearer {token}"
        else:
            user = user or os.environ.get("DATAHUB_USER", "datahub")
            password = password or os.environ.get("DATAHUB_PASSWORD", "datahub")
            raw = f"{user}:{password}".encode()
            self._auth = "Basic " + base64.b64encode(raw).decode()

    # ---------- transport ----------

    def _post_graphql(self, query: str, variables: dict | None = None) -> dict:
        body: dict = {"query": query}
        if variables:
            body["variables"] = variables
        req = urllib.request.Request(
            f"{self.gms_url}/api/graphql",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": self._auth},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as exc:  # pragma: no cover - network failure path
            raise DataHubError(f"GMS HTTP {exc.code}: {exc.read().decode()[:300]}") from exc
        except urllib.error.URLError as exc:  # pragma: no cover - network failure path
            raise DataHubError(f"cannot reach GMS at {self.gms_url}: {exc.reason}") from exc
        if payload.get("errors"):
            raise DataHubError(json.dumps(payload["errors"])[:400])
        return payload.get("data") or {}

    def _get_json(self, path: str) -> dict | list | None:
        """GET a GMS REST path. Returns None on 404 so callers can treat it as absence."""
        req = urllib.request.Request(self.gms_url + path, headers={"Authorization": self._auth})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise DataHubError(f"GMS HTTP {exc.code}: {exc.read().decode()[:300]}") from exc
        except urllib.error.URLError as exc:  # pragma: no cover - network failure path
            raise DataHubError(f"cannot reach GMS at {self.gms_url}: {exc.reason}") from exc

    def _get_aspect(self, urn: str, aspect: str, version: int = 0) -> dict | None:
        """Read one version of an aspect straight from storage.

        Search-index reads lag writes by seconds; aspect reads do not. Anything that must
        be correct immediately after a write goes through here.
        """
        payload = self._get_json(
            f"/aspects/{urllib.parse.quote(urn, safe='')}?aspect={aspect}&version={version}"
        )
        if not isinstance(payload, dict):
            return None
        for value in (payload.get("aspect") or {}).values():
            return value
        return None

    # ---------- reads ----------

    def health(self) -> str:
        data = self._post_graphql("{ me { corpUser { username } } }")
        return ((data.get("me") or {}).get("corpUser") or {}).get("username", "")

    def find_term(self, name: str) -> str | None:
        """Resolve a glossary term's display name to a URN.

        Seeded URNs are opaque UUIDs (`urn:li:glossaryTerm:b2fd91.42266719-…`), so a
        human — or an agent given a metric name — has to search by name. Exact
        case-insensitive name match wins over a fuzzy search hit.
        """
        data = self._post_graphql(
            """
            query findTerm($q: String!) {
              searchAcrossEntities(input: {types: [GLOSSARY_TERM], query: $q, count: 25}) {
                searchResults { entity { urn ... on GlossaryTerm { properties { name } } } }
              }
            }
            """,
            {"q": name},
        )
        results = (data.get("searchAcrossEntities") or {}).get("searchResults") or []
        fallback = None
        for item in results:
            entity = item.get("entity") or {}
            urn = entity.get("urn")
            if not urn:
                continue
            term_name = ((entity.get("properties") or {}).get("name") or "").strip()
            if term_name.lower() == name.strip().lower():
                return urn
            fallback = fallback or urn
        return fallback

    def term_name(self, urn: str) -> str:
        aspect = self._get_aspect(urn, "glossaryTermInfo") or {}
        return aspect.get("name") or urn.rsplit(":", 1)[-1]

    def all_terms(self, max_results: int = 200) -> list[tuple[str, str]]:
        """Every glossary term in the catalog as (urn, display name)."""
        data = self._post_graphql(
            """
            query allTerms($count: Int!) {
              searchAcrossEntities(input: {types: [GLOSSARY_TERM], query: "*", count: $count}) {
                searchResults { entity { urn ... on GlossaryTerm { properties { name } } } }
              }
            }
            """,
            {"count": max_results},
        )
        out: list[tuple[str, str]] = []
        for item in (data.get("searchAcrossEntities") or {}).get("searchResults") or []:
            entity = item.get("entity") or {}
            urn = entity.get("urn")
            if not urn:
                continue
            name = ((entity.get("properties") or {}).get("name") or "").strip()
            out.append((urn, name or urn.rsplit(":", 1)[-1]))
        return out

    def all_term_urns(self, max_results: int = 200) -> list[str]:
        return [urn for urn, _ in self.all_terms(max_results)]

    def all_term_names(self, max_results: int = 200) -> list[str]:
        return [name for _, name in self.all_terms(max_results)]

    def term_revisions(self, urn: str, max_versions: int = 50) -> list[TermRevision]:
        """Full definition history, newest first.

        ⚠️ DataHub's aspect version numbering is not the intuitive one, and getting it
        wrong silently produces a diff against the wrong revision:

            v0  = a *copy* of the current value (same as the highest-numbered version)
            v1  = the OLDEST value
            vN  = the newest — identical to v0

        So the "previous" definition is v(N-1), not v1. Rather than rely on that
        arithmetic we read `systemMetadata.lastObserved` off each version via OpenAPI v3
        and sort by wall-clock time, which is correct regardless of how GMS assigns
        numbers. v0 is dropped as a duplicate of vN.
        """
        entity_type = _entity_type_of(urn)
        quoted = urllib.parse.quote(urn, safe="")
        seen_ts: set[int] = set()
        revisions: list[TermRevision] = []

        for version in range(1, max_versions + 1):
            payload = self._get_json(
                f"/openapi/v3/entity/{entity_type}/{quoted}/glossaryTermInfo"
                f"?version={version}&systemMetadata=true"
            )
            if not isinstance(payload, dict):
                break  # 404 = end of history
            value = payload.get("value") or {}
            meta = payload.get("systemMetadata") or {}
            observed = int(meta.get("lastObserved") or 0)
            if observed and observed in seen_ts:
                continue
            seen_ts.add(observed)
            revisions.append(
                TermRevision(
                    version=version,
                    name=value.get("name", ""),
                    definition=value.get("definition", "") or "",
                    observed_at=observed,
                )
            )

        if not revisions:
            # OpenAPI v3 unavailable — fall back to the legacy aspect route. Ordering
            # there is version-number only, so treat the highest number as newest.
            for version in range(max_versions):
                aspect = self._get_aspect(urn, "glossaryTermInfo", version=version)
                if aspect is None:
                    break
                if version == 0:
                    continue
                revisions.append(
                    TermRevision(
                        version=version,
                        name=aspect.get("name", ""),
                        definition=aspect.get("definition", "") or "",
                        observed_at=0,
                    )
                )

        revisions.sort(key=lambda r: (r.observed_at, r.version), reverse=True)
        return revisions

    def term_timeline(self, urn: str) -> list[TimelineChange]:
        """DOCUMENTATION change events from the Timeline API.

        This is the audit trail: who changed the definition, when, and DataHub's own
        semantic-version verdict on the change. Note the path is `/openapi/v2/...` —
        the v3 equivalent is not routed and 404s.
        """
        path = (
            f"/openapi/v2/timeline/v1/{urllib.parse.quote(urn, safe='')}"
            "?categories=DOCUMENTATION&startTime=-1&endTime=0"
        )
        payload = self._get_json(path)
        if not isinstance(payload, list):
            return []
        changes: list[TimelineChange] = []
        for entry in payload:
            for event in entry.get("changeEvents") or []:
                changes.append(
                    TimelineChange(
                        timestamp=entry.get("timestamp", 0),
                        actor=entry.get("actor", ""),
                        operation=event.get("operation", ""),
                        sem_ver=entry.get("semVer", ""),
                        description=event.get("description", "") or "",
                    )
                )
        changes.sort(key=lambda c: c.timestamp)
        return changes

    def assets_with_term(self, term_urn: str, max_results: int = 100) -> list[TaggedAsset]:
        """Every asset the term is applied to.

        Uses the `glossaryTerms` search filter rather than the relationships API: both
        return the same set, but search also hands back the name and platform in one
        round trip.
        """
        data = self._post_graphql(
            """
            query tagged($u: String!, $count: Int!) {
              searchAcrossEntities(input: {
                query: "*", count: $count,
                orFilters: [{and: [{field: "glossaryTerms", values: [$u]}]}]
              }) {
                searchResults {
                  entity {
                    urn
                    type
                    ... on Dataset { name platform { name } }
                    ... on Chart { properties { name } }
                    ... on Dashboard { properties { name } }
                  }
                }
              }
            }
            """,
            {"u": term_urn, "count": max_results},
        )
        out: list[TaggedAsset] = []
        for item in (data.get("searchAcrossEntities") or {}).get("searchResults") or []:
            entity = item.get("entity") or {}
            props = entity.get("properties") or {}
            out.append(
                TaggedAsset(
                    urn=entity.get("urn", ""),
                    entity_type=entity.get("type", ""),
                    name=entity.get("name") or props.get("name") or "",
                    platform=((entity.get("platform") or {}).get("name") or ""),
                )
            )
        return out

    def downstream_assets(self, urn: str, max_results: int = 100) -> list[DownstreamAsset]:
        """Everything downstream of an asset, to any depth.

        ``degree`` only accepts "1", "2" and "3+" — passing "3" is a 400 from GMS. The
        three of them together mean "all depths", and the returned degree is exact.
        """
        data = self._post_graphql(
            """
            query lineage($urn: String!, $count: Int!) {
              searchAcrossLineage(input: {
                urn: $urn, direction: DOWNSTREAM, query: "*", count: $count,
                orFilters: [{and: [{field: "degree", values: ["1", "2", "3+"]}]}]
              }) {
                searchResults {
                  degree
                  entity {
                    urn
                    type
                    ... on Dataset {
                      name
                      platform { name }
                      ownership { owners { owner { ... on CorpUser { username } ... on CorpGroup { name } } } }
                    }
                    ... on Chart { properties { name } platform { name } }
                    ... on Dashboard { properties { name } platform { name } }
                  }
                }
              }
            }
            """,
            {"urn": urn, "count": max_results},
        )
        out: list[DownstreamAsset] = []
        for item in (data.get("searchAcrossLineage") or {}).get("searchResults") or []:
            entity = item.get("entity") or {}
            props = entity.get("properties") or {}
            owners = [
                (o.get("owner") or {}).get("username") or (o.get("owner") or {}).get("name") or ""
                for o in ((entity.get("ownership") or {}).get("owners") or [])
            ]
            out.append(
                DownstreamAsset(
                    urn=entity.get("urn", ""),
                    entity_type=entity.get("type", ""),
                    degree=item.get("degree", 0),
                    name=entity.get("name") or props.get("name") or "",
                    platform=((entity.get("platform") or {}).get("name") or ""),
                    owners=[o for o in owners if o],
                )
            )
        return out

    # ---------- writes (the "contribute back to the graph" half) ----------

    def update_term_definition(self, term_urn: str, definition: str) -> bool:
        """Edit a term's definition — which is what *creates* a new revision.

        Used by `make drift` to author the demo's history through the real API. Not
        called during an audit; the auditor only reads.
        """
        data = self._post_graphql(
            "mutation upd($u: String!, $d: String!) { updateDescription(input: {resourceUrn: $u, description: $d}) }",
            {"u": term_urn, "d": definition},
        )
        return bool(data.get("updateDescription"))

    def create_document(self, title: str, text: str, related_urns: list[str]) -> str:
        """Persist the audit as a Context Document linked to the affected assets.

        This is what makes the finding durable and inheritable: the next person who opens
        one of these dashboards in DataHub sees *that* its metric was redefined, *when*,
        and *what the definition used to say* — without re-running anything.

        ``createDocument`` returns a bare String urn; selecting subfields on it is a
        validation error.
        """
        data = self._post_graphql(
            "mutation cd($input: CreateDocumentInput!) { createDocument(input: $input) }",
            {
                "input": {
                    "title": title[:500],
                    "subType": "REVIEW",
                    "contents": {"text": text[:20000]},
                    "relatedAssets": related_urns,
                }
            },
        )
        return data.get("createDocument", "")

    def raise_incident(self, urns: list[str], title: str, description: str) -> str:
        """Raise a DataHub incident. Verified working on OSS Core v1.5.0.6.

        (The `datahub-quality` skill doc claims incidents are Cloud-only. They are not —
        see our upstream correction PR, linked in the README.)
        """
        data = self._post_graphql(
            "mutation raise($input: RaiseIncidentInput!) { raiseIncident(input: $input) }",
            {
                "input": {
                    "type": "OPERATIONAL",
                    "title": title[:500],
                    "description": description[:5000],
                    "resourceUrns": urns,
                }
            },
        )
        return data.get("raiseIncident", "")

    def add_tag(self, urn: str, tag: str, description: str = "Applied by semantic-drift-auditor") -> bool:
        tag_urn = f"urn:li:tag:{tag}"
        if not self._tag_exists(tag_urn):
            self._post_graphql(
                "mutation ct($input: CreateTagInput!) { createTag(input: $input) }",
                {"input": {"id": tag, "name": tag, "description": description}},
            )
        data = self._post_graphql(
            "mutation at($input: TagAssociationInput!) { addTag(input: $input) }",
            {"input": {"tagUrn": tag_urn, "resourceUrn": urn}},
        )
        return bool(data.get("addTag"))

    def _tag_exists(self, tag_urn: str) -> bool:
        try:
            return self._get_aspect(tag_urn, "tagKey") is not None
        except DataHubError:
            return False
