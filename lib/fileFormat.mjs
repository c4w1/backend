import path from "node:path";
import AdmZip from "adm-zip";

/**
 * The file type of a download, in the vocabulary of download.format: csv, tsv, xlsx,
 * xls, accdb, or zip/<the file inside>. The bytes decide; the name only tells a TSV
 * from a CSV. pythonscripts/export_previews.py does the same for the static previews.
 */

const OLE2_MAGIC = Buffer.from([0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1]);
// What a zip's table can be, best first.
const TABLE_TYPES = ["csv", "tsv", "xlsx", "xls"];

function isZip(data) {
  return data.length >= 4 && data[0] === 0x50 && data[1] === 0x4b && data[2] === 0x03 && data[3] === 0x04;
}

function extension(name) {
  return path.extname(name).slice(1).toLowerCase();
}

function zipFiles(data) {
  return new AdmZip(data).getEntries().filter((e) => !e.isDirectory);
}

function zipFormat(files) {
  if (files.some((e) => e.entryName === "xl/workbook.xml")) return "xlsx";
  const inner =
    TABLE_TYPES.find((type) => files.some((e) => extension(e.entryName) === type)) ??
    extension([...files].sort((a, b) => b.header.size - a.header.size)[0]?.entryName ?? "");
  return inner ? `zip/${inner}` : "zip";
}

export function detectFileFormat(data, filename = "") {
  if (isZip(data)) return zipFormat(zipFiles(data));
  if (data.subarray(0, OLE2_MAGIC.length).equals(OLE2_MAGIC)) return "xls";
  return extension(filename) === "tsv" ? "tsv" : "csv";
}

/** The first file of `type` in a zip, by name, as bytes. */
export function readFromZip(data, type) {
  const entry = zipFiles(data)
    .filter((e) => extension(e.entryName) === type)
    .sort((a, b) => a.entryName.localeCompare(b.entryName))[0];
  return entry ? entry.getData() : null;
}
