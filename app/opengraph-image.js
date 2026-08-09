import { ImageResponse } from "next/og";

export const alt = "RepoCharter — One repo charter. Every coding agent.";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function OpenGraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          position: "relative",
          overflow: "hidden",
          color: "#f6f8fc",
          background: "#080b10",
          fontFamily: "Arial, sans-serif",
          padding: "72px 78px",
        }}
      >
        <div
          style={{
            position: "absolute",
            width: 640,
            height: 640,
            right: -180,
            top: -250,
            borderRadius: 999,
            background: "rgba(43, 101, 255, .28)",
            opacity: 0.65,
          }}
        />
        <div
          style={{
            position: "absolute",
            width: 520,
            height: 520,
            right: -60,
            bottom: -350,
            borderRadius: 999,
            background: "rgba(77, 224, 183, .22)",
            opacity: 0.55,
          }}
        />
        <div style={{ display: "flex", flexDirection: "column", justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 18, fontSize: 34, fontWeight: 750 }}>
            <div style={{ width: 50, height: 50, border: "3px solid #4d82ff", borderRadius: 13, display: "flex", alignItems: "center", justifyContent: "center", color: "#4de0b7", fontSize: 28 }}>R</div>
            <span style={{ display: "flex" }}>Repo<span style={{ color: "#4d82ff" }}>Charter</span></span>
          </div>
          <div style={{ display: "flex", flexDirection: "column" }}>
            <div style={{ display: "flex", flexDirection: "column", fontSize: 76, fontWeight: 780, letterSpacing: "-3.5px", lineHeight: 1.04, maxWidth: 890 }}>
              <span>One repo charter.</span>
              <span>Every coding agent.</span>
            </div>
            <div style={{ marginTop: 28, fontSize: 27, color: "#aeb8c9", maxWidth: 820, lineHeight: 1.4 }}>
              Portable context and tested guardrails for agentic development.
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 18, fontSize: 23, color: "#4de0b7" }}>
            repocharter.com
            <span style={{ width: 5, height: 5, borderRadius: 99, background: "#4d82ff" }} />
            209 tests · zero CLI dependencies
          </div>
        </div>
      </div>
    ),
    size,
  );
}
