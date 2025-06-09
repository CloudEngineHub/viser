import React, { useEffect, useRef, useState, useContext, useCallback } from "react";
import { ViewerContext } from "./ViewerContext";
import { Button, Group, Popover } from "@mantine/core";
import Webcam from "react-webcam";

export function CameraStream() {
  const viewer = useContext(ViewerContext)!;
  const webcamRef = useRef<Webcam>(null);
  const intervalRef = useRef<NodeJS.Timeout | null>(null);
  const [config, setConfig] = useState(viewer.mutable.current.cameraStreamConfig);
  const [popoverOpen, setPopoverOpen] = useState(false);

  useEffect(() => {
    const checkConfigChanges = () => {
      const currentConfig = viewer.mutable.current.cameraStreamConfig;
      if (currentConfig.enabled !== config.enabled) {
        console.log("📝 Config enabled changed to:", currentConfig.enabled);
        setConfig({ ...currentConfig });
      }
    };
    const interval = setInterval(checkConfigChanges, 1000);
    return () => clearInterval(interval);
  }, [config.enabled]);

  const captureFrame = useCallback(() => {
    if (!webcamRef.current) {
      console.log("🎥 No webcam", webcamRef.current);
      return;
    }

    const imageSrc = webcamRef.current.getScreenshot();
    if (!imageSrc) {
      console.log("🎥 No image data from webcam");
      return;
    }

    console.log("📸 Captured frame, sending to server...");

    // Convert base64 to Uint8Array
    const byteString = atob(imageSrc.split(',')[1]);
    const ab = new ArrayBuffer(byteString.length);
    const ia = new Uint8Array(ab);
    for (let i = 0; i < byteString.length; i++) {
      ia[i] = byteString.charCodeAt(i);
    }
    console.log("🎥 Image src:", ia);
    const message = {
      type: "CameraStreamFrameMessage" as const,
      frame_data: ia,
      timestamp: Date.now() / 1000,
      width: 640,
      height: 480,
      format: "image/jpeg" as const,
    };

    viewer.mutable.current.sendMessage(message);
    console.log("✅ Frame sent");
  }, [viewer]);

  // Start streaming
  const handleUserMedia = useCallback(() => {
    console.log("🎥 Webcam ready, starting capture...");

    if (intervalRef.current) clearInterval(intervalRef.current);

    intervalRef.current = setInterval(() => {
      captureFrame();
    }, 1000 / (config.captureFps || 10));

    console.log(`🎥 Capture started at ${config.captureFps || 10} FPS`);
  }, [captureFrame, config.captureFps]);

  // Handle errors
  const handleUserMediaError = useCallback((error: any) => {
    console.error("❌ Webcam error:", error);
  }, []);

  if (!config.enabled) {
    console.log("🎥 Camera disabled, not rendering");
    return null;
  }

  return (
    <Group justify="center">
      <Webcam
        ref={webcamRef}
        audio={false}
        // width={100}
        // height={000}
        screenshotFormat="image/jpeg"
        videoConstraints={{ width: 1000, height: 1000, facingMode: "user" }}
        onUserMedia={handleUserMedia}
        onUserMediaError={handleUserMediaError}
        mirrored={false}
        // This is a hack -- display: none doesn't work, it seems to fetch the current webcam render.
        style={{ position: "fixed", left: "-9999px", top: "-9999px" }}
      />

      {/* UI */}
      {/* <Popover opened={popoverOpen} onChange={setPopoverOpen}>
        <Popover.Target>
          <Button
            onClick={() => setPopoverOpen(!popoverOpen)}
            color="blue"
          >
            📹 Camera
          </Button>
        </Popover.Target>
        <Popover.Dropdown>
          <div style={{ padding: "10px", color: "black" }}>
            <div>FPS: {config.captureFps || 10}</div>
            <div>Config Enabled: {config.enabled ? "Yes" : "No"}</div>
          </div>
        </Popover.Dropdown>
      </Popover> */}
    </Group>
  );
}