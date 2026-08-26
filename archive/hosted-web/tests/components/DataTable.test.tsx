// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DataTable } from "@/components/ui";

type Row = { id: string; name: string; fit: number };
const rows: Row[] = [
  { id: "1", name: "Maya Rao", fit: 94 },
  { id: "2", name: "Jordan Kim", fit: 88 },
];
const columns = [
  { key: "name", header: "Name", sortable: true },
  { key: "fit", header: "Fit", numeric: true },
];

describe("DataTable", () => {
  it("renders headers and a row per item", () => {
    render(<DataTable columns={columns} rows={rows} getRowId={(r) => r.id} />);
    expect(screen.getByText("Name")).toBeInTheDocument();
    expect(screen.getByText("Maya Rao")).toBeInTheDocument();
    expect(screen.getByText("Jordan Kim")).toBeInTheDocument();
  });

  it("applies tabular-nums to numeric cells", () => {
    render(<DataTable columns={columns} rows={rows} getRowId={(r) => r.id} />);
    expect(screen.getByText("94").className).toContain("tabular-nums");
  });

  it("calls onSort when a sortable header is clicked", () => {
    let sorted = "";
    render(
      <DataTable columns={columns} rows={rows} getRowId={(r) => r.id} onSort={(k) => (sorted = k)} />,
    );
    fireEvent.click(screen.getByText("Name"));
    expect(sorted).toBe("name");
  });
});
