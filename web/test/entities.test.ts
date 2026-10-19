import { describe, expect, it } from "vitest";
import { uniqueEntities } from "../src/lib/shapes";

describe("entity pickers", () => {
  it("offer each entity once, by id, the first row kept", () => {
    const rows = [
      { entity_id: "ex:concept-memory", name: "memory", kind: "concept", display_name: "Memory" },
      { entity_id: "ex:software-gateway", name: "gateway", kind: "software" },
      { entity_id: "ex:concept-memory", name: "memory", kind: "concept", display_name: "memory" },
    ];
    expect(uniqueEntities(rows).map((e) => [e.entity_id, e.display_name])).toEqual([
      ["ex:concept-memory", "Memory"],
      ["ex:software-gateway", undefined],
    ]);
    expect(uniqueEntities(undefined)).toEqual([]);
  });
});
