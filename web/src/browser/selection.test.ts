import { NONE, click, prune } from "./selection";

const order = [10, 20, 30, 40, 50];
const plain = { ctrl: false, shift: false };

describe("selection", () => {
  it("selects one row on a plain click", () => {
    const s = click(click(NONE, order, 20, plain), order, 40, plain);
    expect([...s.ids]).toEqual([40]);
    expect(s.current).toBe(40);
  });

  it("toggles with Ctrl", () => {
    let s = click(NONE, order, 20, plain);
    s = click(s, order, 40, { ctrl: true, shift: false });
    expect([...s.ids].sort()).toEqual([20, 40]);
    s = click(s, order, 20, { ctrl: true, shift: false });
    expect([...s.ids]).toEqual([40]);
  });

  it("selects a range with Shift, in either direction", () => {
    let s = click(NONE, order, 40, plain);
    s = click(s, order, 20, { ctrl: false, shift: true });
    expect([...s.ids]).toEqual([20, 30, 40]);
    expect(s.anchor).toBe(40);
    s = click(s, order, 50, { ctrl: false, shift: true });
    expect([...s.ids]).toEqual([40, 50]);
  });

  it("adds a range with Ctrl+Shift", () => {
    let s = click(NONE, order, 10, plain);
    s = click(s, order, 30, { ctrl: true, shift: false });
    s = click(s, order, 50, { ctrl: true, shift: true });
    expect([...s.ids].sort()).toEqual([10, 30, 40, 50]);
  });

  it("drops rows that left the table", () => {
    let s = click(NONE, order, 20, plain);
    s = click(s, order, 30, { ctrl: true, shift: false });
    const p = prune(s, [10, 20, 40]);
    expect([...p.ids]).toEqual([20]);
    expect(p.current).toBeNull();
    expect(prune(p, [10, 20, 40])).toBe(p);
  });
});
