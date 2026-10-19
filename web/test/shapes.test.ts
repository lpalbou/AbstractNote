import { describe, expect, it } from "vitest";
import { claimsById, thenNowView } from "../src/lib/shapes";
import { claimStatusLine } from "../src/lib/words";

const claim = (id: string, obj: string, status = "active") => ({ claim_id: id, subject_id: "ex:person-dana", subject_name: "Dana", predicate: "schema:workLocation", object_id: null, object_literal: obj, observed_at: "2026-09-02T09:05:00Z", status, note_id: "nt_a" });

describe("then versus now forms", () => {
  it("entity form with summaries and a supersession", () => {
    const v = thenNowView({
      earliest: { at: "2026-07-23T14:30:00Z", summary: "Leads LUT from Lyon.", note_id: "nt_a" },
      latest: { at: "2026-09-02T09:05:00Z", summary: "Leads LUT from Paris." },
      turning_points: [{ at: "2026-09-02T09:05:00Z", kind: "supersede", text: "Dana work location Lyon", claim_id: "cl_1", note_id: "nt_b" }],
    })!;
    expect(v.earliest).toEqual({ at: "2026-07-23T14:30:00Z", lines: ["Leads LUT from Lyon."] });
    expect(v.turning[0]).toMatchObject({ text: "Dana work location Lyon", claim_id: "cl_1" });
  });

  it("entity form without summaries falls back to the claims (live notebook, W1)", () => {
    const v = thenNowView({ earliest: null, latest: null, earliest_claims: [claim("cl_1", "Lyon")], current_beliefs: [claim("cl_2", "Paris")], turning_points: [] })!;
    expect(v.earliest?.lines).toEqual(["Dana work location Lyon"]);
    expect(v.latest?.lines).toEqual(["Dana work location Paris"]);
  });

  it("relation form: claim briefs and turning points that only name a claim", () => {
    const claims = [claim("cl_1", "Lyon", "superseded"), claim("cl_2", "Paris")];
    const v = thenNowView({ earliest: [{ claim_id: "cl_1", text: "Dana schema:workLocation Lyon", observed_at: "2026-07-23T14:30:00Z" }], latest: [], turning_points: [{ at: "2026-09-02T09:05:00Z", kind: "supersede", claim_id: "cl_1" }] }, claimsById(claims))!;
    expect(v.earliest?.lines).toEqual(["Dana schema:workLocation Lyon"]);
    expect(v.latest).toBeNull();
    expect(v.turning[0].text).toBe("Dana work location Lyon (replaced)");
  });

  it("recall form: empty strings mean nothing to show; strings and {change} points render", () => {
    expect(thenNowView({ earliest: "", latest: "", turning_points: [] })).toBeNull();
    const v = thenNowView({ earliest: "40k", latest: "25k", turning_points: [{ note_id: "nt_b", occurred_at: "2026-09-14T10:00:00Z", change: "Budget cut" }] })!;
    expect(v.earliest?.lines).toEqual(["40k"]);
    expect(v.turning[0]).toMatchObject({ at: "2026-09-14T10:00:00Z", text: "Budget cut", note_id: "nt_b" });
  });
});

describe("the claim card's status line", () => {
  it("a withdrawn claim shows no window, even when its stored end is before its start (review 02 m3)", () => {
    const line = claimStatusLine({ status: "retracted", observed_at: "2026-10-19T09:12:00Z", valid_from: "2026-10-19T09:12:00Z", valid_until: "2026-10-19T00:48:40Z" });
    expect(line.startsWith("Withdrawn · noted ")).toBe(true);
    expect(line).not.toContain("until");
  });
  it("an end is shown only after the start", () => {
    expect(claimStatusLine({ status: "superseded", observed_at: "2026-01-05T10:00:00Z", valid_from: "2026-01-05T10:00:00Z", valid_until: "2026-03-01T10:00:00Z" })).toContain(" until ");
    expect(claimStatusLine({ status: "superseded", observed_at: "2026-01-05T10:00:00Z", valid_from: "2026-01-05T10:00:00Z", valid_until: "2026-01-04T10:00:00Z" })).not.toContain("until");
    expect(claimStatusLine({ status: "active", observed_at: "2026-01-05T10:00:00Z" }).startsWith("Believed · since ")).toBe(true);
  });
});
