import { ImageResponse } from "next/og";

export const alt = "Market Research Agent — 带来源、区分事实与推测的加密与美股研究";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function OpenGraphImage() {
  return new ImageResponse(
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        padding: 80,
        background: "#ffffff",
        color: "#0a0a0a",
      }}
    >
      <div style={{ display: "flex", alignItems: "center" }}>
        <div
          style={{
            width: 64,
            height: 64,
            borderRadius: 16,
            background: "#3538cd",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <svg width="40" height="40" viewBox="0 0 32 32">
            <polyline
              points="6.5,21.5 12,15.5 16.5,19 25.5,9.5"
              fill="none"
              stroke="#ffffff"
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </div>
        <div
          style={{
            marginLeft: 24,
            fontSize: 44,
            fontWeight: 650,
            letterSpacing: "-0.03em",
          }}
        >
          Market Research Agent
        </div>
      </div>
      <div
        style={{
          marginTop: 28,
          fontSize: 28,
          color: "#52525b",
          lineHeight: 1.4,
          maxWidth: 920,
        }}
      >
        带来源、区分事实与推测的加密与美股研究
      </div>
    </div>,
    size,
  );
}
