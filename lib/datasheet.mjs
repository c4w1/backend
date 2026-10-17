function normalizeTags(descriptionTags) {
  if (!descriptionTags) return [];
  if (Array.isArray(descriptionTags)) return descriptionTags;
  if (typeof descriptionTags === "object") {
    return Object.entries(descriptionTags)
      .filter(([, active]) => active)
      .map(([tag]) => tag);
  }
  return [];
}

function inferYearRange(source) {
  const variables = source.variables ?? [];
  const years = new Set();
  for (const name of variables) {
    const match = String(name).match(/(19|20)\d{2}/g);
    if (match) match.forEach((y) => years.add(y));
  }
  if (years.size >= 2) {
    const sorted = [...years].sort();
    return `${sorted[0]}–${sorted[sorted.length - 1]}`;
  }
  if (years.size === 1) return [...years][0];
  if (source.notes) {
    const noteMatch = source.notes.match(/(19|20)\d{2}/g);
    if (noteMatch?.length) {
      const sorted = [...new Set(noteMatch)].sort();
      return sorted.length > 1 ? `${sorted[0]}–${sorted[sorted.length - 1]}` : sorted[0];
    }
  }
  return "Not specified";
}

function inferWhere(source) {
  const filters = source.filters ?? {};
  const parts = [];
  if (filters.county) parts.push("county-level");
  if (filters.state) parts.push("state-level");
  if (filters.zipcode) parts.push("ZIP code-level");
  if (filters.year) parts.push("time-series");

  const variables = (source.variables ?? []).map((v) => String(v).toUpperCase());
  if (variables.some((v) => v.includes("COUNTY") || v === "CTYNAME")) parts.push("county-level");
  if (variables.some((v) => v === "STATE" || v === "STNAME")) parts.push("state-level");

  if (parts.length === 0) return "United States";
  return `United States (${[...new Set(parts)].join(", ")})`;
}

export function buildDatasheet(source) {
  const provider = source.provider ?? {};
  const download = source.download ?? {};
  const tags = normalizeTags(source.description_tags);

  return {
    who: [provider.name, provider.agency].filter(Boolean).join(" — ") || "Not specified",
    how: [download.description, source.notes].filter(Boolean).join(" ") || "Not specified",
    where: inferWhere(source),
    why: tags.length > 0 ? `${source.title}: ${tags.join(", ")}` : source.title || "Not specified",
    when: inferYearRange(source),
  };
}

export function getPedagogicalTags(source, limit = 8) {
  const tags = [];
  for (const [key, value] of Object.entries(source)) {
    if (!key.endsWith("_tags") || key === "description_tags") continue;
    if (typeof value !== "object" || value === null) continue;
    for (const [tagName, active] of Object.entries(value)) {
      if (active) tags.push(tagName.replace(/-/g, " "));
    }
  }
  return tags.slice(0, limit);
}

export function getGeographicTags(source) {
  const filters = source.filters ?? {};
  const tags = [];
  if (filters.state) tags.push("state");
  if (filters.county) tags.push("county");
  if (filters.zipcode) tags.push("zipcode");
  if (filters.year) tags.push("year");

  const variables = (source.variables ?? []).map((v) => String(v).toUpperCase());
  if (variables.some((v) => v.includes("COUNTY") || v === "CTYNAME") && !tags.includes("county")) {
    tags.push("county");
  }
  if (variables.some((v) => v === "STATE" || v === "STNAME") && !tags.includes("state")) {
    tags.push("state");
  }

  return tags;
}
