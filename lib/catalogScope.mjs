/**
 * What the catalog leaves out: CDL is a library of local data (web#42).
 *
 * A source is national-level when its hand-coded `filters` show no state, county or
 * ZIP breakdown. This is pythonscripts/catalog_scope.py for the API and the website.
 */

const BREAKDOWN_FILTERS = ["state", "county", "zipcode"];

/** Missing filters can't be judged, so they aren't national-level; see checkCatalogScope. */
export function isNationalLevel(filters) {
  if (!filters || Object.keys(filters).length === 0) return false;
  return !BREAKDOWN_FILTERS.some((name) => filters[name]);
}

/** The sources that break the rule: national-level, or with no filters to check. */
export function checkCatalogScope(sources) {
  const national = [];
  const missingFilters = [];
  for (const source of sources) {
    if (source.filters && Object.keys(source.filters).length > 0) {
      if (isNationalLevel(source.filters)) national.push(source.id);
    } else {
      missingFilters.push(source.id);
    }
  }
  return { national, missingFilters };
}
