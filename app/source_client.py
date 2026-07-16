from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from .config import Settings


class SourceAPIError(RuntimeError):
    """원본 사이트 호출 또는 응답 형식 오류."""


@dataclass(slots=True)
class SourceSearchResult:
    companies: list[dict[str, Any]]
    total_found: int
    detail_succeeded: int
    detail_failed: int
    truncated: bool
    warnings: list[str]


@dataclass(slots=True)
class BulkSearchResult:
    companies: list[dict[str, Any]]
    site_total: int
    requested_count: int
    matched_count: int
    unmatched_names: list[str]
    detail_succeeded: int
    detail_failed: int
    warnings: list[str]


FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "source_id": ("id", "account_id", "guid"),
    "name": ("accountname", "company_name", "name"),
    "industry": ("business", "industry"),
    "item": ("item",),
    "representative": ("ceo", "representative"),
    "founded_year": ("foundation_year", "founded_year"),
    "employee_count": ("employees_cnt", "employee_count"),
    "phone": ("telephone", "phone", "tel"),
    "address": ("address",),
    "email": ("email",),
    "homepage": ("homepage", "website"),
    "business_area": ("biz_area", "business_area"),
    "summary": ("inproduction", "summary"),
    "description": ("description",),
    "product_service": ("productservice", "product_service"),
    "interest_category": ("interest_category",),
    "logo_url": ("virtual_path", "logo_url"),
}


DETAIL_ONLY_FIELDS = {"employee_count", "phone", "email", "description", "product_service"}


def normalize_company_name(value: Any) -> str:
    """Normalize corporate prefixes and punctuation for bulk name matching."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    text = re.sub(r"^\s*\d+\s*[.)-]\s*", "", text)
    text = text.replace("주식회사", "")
    text = re.sub(r"^\s*\(?\s*주\s*\)?\s*", "", text)
    text = re.sub(r"\s*\(?\s*주\s*\)?\s*$", "", text)
    return re.sub(r"[^0-9a-z가-힣]", "", text)


def match_company_names(
    company_names: list[str], raw_list: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Match pasted names to the site's complete list while preserving input order."""
    by_name: dict[str, list[dict[str, Any]]] = {}
    for item in raw_list:
        key = normalize_company_name(item.get("accountname"))
        if key:
            by_name.setdefault(key, []).append(item)

    selected: list[dict[str, Any]] = []
    unmatched: list[str] = []
    seen_ids: set[str] = set()
    for name in company_names:
        candidates = by_name.get(normalize_company_name(name), [])
        candidate = next(
            (item for item in candidates if str(item.get("id") or "") not in seen_ids),
            candidates[0] if candidates else None,
        )
        if candidate is None:
            unmatched.append(name)
            continue
        source_id = str(candidate.get("id") or "")
        if source_id and source_id not in seen_ids:
            selected.append(candidate)
            seen_ids.add(source_id)
    return selected, unmatched


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    return text or None


def normalize_company(raw: dict[str, Any], detail_complete: bool = True) -> dict[str, Any]:
    """원본 응답을 애플리케이션의 고정 필드명으로 정규화한다."""
    normalized: dict[str, Any] = {}
    available_fields: list[str] = []
    for target, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            if alias in raw:
                normalized[target] = _clean(raw.get(alias))
                available_fields.append(target)
                break
        else:
            normalized[target] = None

    if not normalized["source_id"]:
        raise SourceAPIError("원본 응답에 기업 식별자(id)가 없습니다.")
    if not normalized["name"]:
        normalized["name"] = f"이름 없음 ({normalized['source_id']})"

    employee = normalized.get("employee_count")
    if employee:
        match = re.search(r"-?\d[\d,]*", employee)
        normalized["employee_count"] = int(match.group(0).replace(",", "")) if match else None

    normalized["source_url"] = (
        f"/Pages/AccountDetail.aspx?id={normalized['source_id']}"
    )
    normalized["detail_complete"] = detail_complete
    normalized["available_fields"] = available_fields
    normalized["raw"] = raw
    return normalized


class HiSeoulClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "HiSeoul-Local-Company-Manager/1.0",
            "Referer": f"{self.settings.source_base_url}/Pages/AccountList.aspx",
        }
        if self.settings.source_cookie:
            headers["Cookie"] = self.settings.source_cookie
        if self.settings.source_api_key:
            headers[self.settings.source_api_key_header] = self.settings.source_api_key
        headers.update(self.settings.source_headers)
        return headers

    @staticmethod
    def _unwrap(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceAPIError("원본 사이트가 JSON이 아닌 응답을 반환했습니다.") from exc
        data = payload.get("d", payload) if isinstance(payload, dict) else payload
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError as exc:
                raise SourceAPIError("원본 사이트의 d 응답을 해석할 수 없습니다.") from exc
        if not isinstance(data, dict):
            raise SourceAPIError("원본 사이트 응답 형식이 예상과 다릅니다.")
        if data.get("IsOK") is False:
            raise SourceAPIError(str(data.get("Message") or "원본 사이트가 요청을 거부했습니다."))
        return data

    async def _post(
        self, client: httpx.AsyncClient, path: str, payload: dict[str, Any], attempts: int = 3
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = await client.post(path, json=payload)
                response.raise_for_status()
                return self._unwrap(response)
            except (httpx.HTTPError, SourceAPIError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.5 * (2**attempt))
        raise SourceAPIError(f"원본 사이트 호출 실패: {last_error}") from last_error

    async def search(
        self,
        query: str,
        *,
        item: str = "",
        interest_category: str = "",
        address: str = "",
        export_cd: str = "",
        sort: str = "1",
        include_details: bool = True,
    ) -> SourceSearchResult:
        query = query.strip()
        if not query and not self.settings.allow_empty_source_search:
            raise SourceAPIError(
                "원본 사이트 전체 조회는 기본적으로 차단되어 있습니다. 기업명을 한 글자 이상 입력하세요."
            )

        timeout = httpx.Timeout(self.settings.source_timeout_seconds)
        async with httpx.AsyncClient(
            base_url=self.settings.source_base_url,
            headers=self._headers(),
            timeout=timeout,
            verify=self.settings.source_verify_tls,
            follow_redirects=True,
        ) as client:
            list_data = await self._post(
                client,
                self.settings.source_list_path,
                {
                    "args": {
                        "sch_txt": query,
                        "item": item,
                        "interest_category": interest_category,
                        "address": address,
                        "export_cd": export_cd,
                        "sort": sort,
                    }
                },
            )
            raw_list = list_data.get("lists") or []
            if not isinstance(raw_list, list):
                raise SourceAPIError("원본 목록 응답의 lists가 배열이 아닙니다.")

            total_found = len(raw_list)
            truncated = total_found > self.settings.source_max_details
            selected = raw_list[: self.settings.source_max_details]
            warnings: list[str] = []
            if truncated:
                warnings.append(
                    f"검색 결과 {total_found:,}개 중 설정된 최대 {len(selected):,}개만 동기화했습니다. "
                    "더 구체적인 기업명으로 검색하거나 SOURCE_MAX_DETAILS를 조정하세요."
                )

            if not include_details:
                return SourceSearchResult(
                    companies=[normalize_company(item, detail_complete=False) for item in selected],
                    total_found=total_found,
                    detail_succeeded=0,
                    detail_failed=0,
                    truncated=truncated,
                    warnings=warnings,
                )

            semaphore = asyncio.Semaphore(self.settings.source_max_concurrency)

            async def fetch_detail(list_item: dict[str, Any]) -> tuple[dict[str, Any], bool, str | None]:
                source_id = str(list_item.get("id") or "").strip()
                if not source_id:
                    return list_item, False, "기업 식별자가 없어 상세정보를 조회하지 못했습니다."
                async with semaphore:
                    if self.settings.source_request_delay_ms:
                        await asyncio.sleep(self.settings.source_request_delay_ms / 1000)
                    try:
                        detail_data = await self._post(
                            client,
                            self.settings.source_detail_path,
                            {"guid": source_id},
                        )
                        lists = detail_data.get("lists") or {}
                        item1 = lists.get("Item1") if isinstance(lists, dict) else None
                        detail = item1[0] if isinstance(item1, list) and item1 else {}
                        if not detail:
                            raise SourceAPIError("상세 응답 Item1이 비어 있습니다.")
                        merged = {**list_item, **detail}
                        return merged, True, None
                    except SourceAPIError as exc:
                        return list_item, False, f"{list_item.get('accountname') or source_id}: {exc}"

            fetched = await asyncio.gather(*(fetch_detail(item) for item in selected))
            companies: list[dict[str, Any]] = []
            succeeded = 0
            failed = 0
            for raw, complete, warning in fetched:
                companies.append(normalize_company(raw, detail_complete=complete))
                if complete:
                    succeeded += 1
                else:
                    failed += 1
                    if warning and len(warnings) < 8:
                        warnings.append(warning)
            if failed > 7:
                warnings.append(f"상세정보 조회 실패가 총 {failed:,}건 발생했습니다.")

            return SourceSearchResult(
                companies=companies,
                total_found=total_found,
                detail_succeeded=succeeded,
                detail_failed=failed,
                truncated=truncated,
                warnings=warnings,
            )

    async def bulk_search(
        self,
        company_names: list[str],
        *,
        sync_all: bool = False,
        include_details: bool = True,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> BulkSearchResult:
        """Load the site list once, match pasted names, then fetch matched details."""

        def progress(current: int, total: int, message: str) -> None:
            if progress_callback:
                progress_callback(current, total, message)

        progress(0, 0, "하이서울기업 전체 목록을 불러오는 중입니다.")
        timeout = httpx.Timeout(self.settings.source_timeout_seconds)
        async with httpx.AsyncClient(
            base_url=self.settings.source_base_url,
            headers=self._headers(),
            timeout=timeout,
            verify=self.settings.source_verify_tls,
            follow_redirects=True,
        ) as client:
            list_data = await self._post(
                client,
                self.settings.source_list_path,
                {
                    "args": {
                        "sch_txt": "",
                        "item": "",
                        "interest_category": "",
                        "address": "",
                        "export_cd": "",
                        "sort": "1",
                    }
                },
            )
            raw_list = list_data.get("lists") or []
            if not isinstance(raw_list, list):
                raise SourceAPIError("원본 전체 목록 응답의 lists가 배열이 아닙니다.")

            site_total = len(raw_list)
            progress(0, site_total, f"전체 {site_total:,}개 기업과 입력 목록을 비교하는 중입니다.")
            if sync_all:
                selected = raw_list.copy()
                unmatched: list[str] = []
                requested_count = site_total
            else:
                selected, unmatched = match_company_names(company_names, raw_list)
                requested_count = len(company_names)

            warnings: list[str] = []
            if len(selected) > self.settings.source_bulk_max_details:
                original_count = len(selected)
                selected = selected[: self.settings.source_bulk_max_details]
                warnings.append(
                    f"매칭 결과 {original_count:,}개 중 설정된 최대 "
                    f"{self.settings.source_bulk_max_details:,}개만 처리했습니다."
                )

            matched_count = len(selected)
            if not include_details:
                progress(matched_count, matched_count, "기업명 매칭을 완료했습니다.")
                return BulkSearchResult(
                    companies=[normalize_company(item, detail_complete=False) for item in selected],
                    site_total=site_total,
                    requested_count=requested_count,
                    matched_count=matched_count,
                    unmatched_names=unmatched,
                    detail_succeeded=0,
                    detail_failed=0,
                    warnings=warnings,
                )

            progress(0, matched_count, f"매칭된 {matched_count:,}개 기업의 상세정보를 수집합니다.")
            semaphore = asyncio.Semaphore(self.settings.source_max_concurrency)

            async def fetch_detail(
                list_item: dict[str, Any],
            ) -> tuple[dict[str, Any], bool, str | None]:
                source_id = str(list_item.get("id") or "").strip()
                if not source_id:
                    return list_item, False, "기업 식별자가 없어 상세정보를 조회하지 못했습니다."
                async with semaphore:
                    if self.settings.source_request_delay_ms:
                        await asyncio.sleep(self.settings.source_request_delay_ms / 1000)
                    try:
                        detail_data = await self._post(
                            client,
                            self.settings.source_detail_path,
                            {"guid": source_id},
                        )
                        lists = detail_data.get("lists") or {}
                        item1 = lists.get("Item1") if isinstance(lists, dict) else None
                        detail = item1[0] if isinstance(item1, list) and item1 else {}
                        if not detail:
                            raise SourceAPIError("상세 응답 Item1이 비어 있습니다.")
                        return {**list_item, **detail}, True, None
                    except Exception as exc:
                        return (
                            list_item,
                            False,
                            f"{list_item.get('accountname') or source_id}: {exc}",
                        )

            tasks = [asyncio.create_task(fetch_detail(item)) for item in selected]
            companies: list[dict[str, Any]] = []
            succeeded = 0
            failed = 0
            completed = 0
            for task in asyncio.as_completed(tasks):
                raw, complete, warning = await task
                companies.append(normalize_company(raw, detail_complete=complete))
                completed += 1
                if complete:
                    succeeded += 1
                else:
                    failed += 1
                    if warning and len(warnings) < 20:
                        warnings.append(warning)
                progress(
                    completed,
                    matched_count,
                    f"상세정보 수집 중 · {completed:,}/{matched_count:,}",
                )

            if failed > 19:
                warnings.append(f"상세정보 조회 실패가 총 {failed:,}건 발생했습니다.")
            return BulkSearchResult(
                companies=companies,
                site_total=site_total,
                requested_count=requested_count,
                matched_count=matched_count,
                unmatched_names=unmatched,
                detail_succeeded=succeeded,
                detail_failed=failed,
                warnings=warnings,
            )
