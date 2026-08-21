import cv2
import time

def main():
    # 0 is typically the default internal webcam on Ubuntu.
    # Change to 1 or 2 if you have external cameras attached.
    cap = cv2.VideoCapture(0)
    
    if not cap.isOpened():
        print("[-] Error: Cannot open webcam.")
        return

    # Initialize OpenCV's native QR code detector
    detector = cv2.QRCodeDetector()
    
    print("[+] Webcam initialized. Hold your phone up to the camera.")
    print("[+] Press 'q' to quit.\n")
    
    while True:
        # --- 1. Camera I/O ---
        start_capture = time.perf_counter()
        ret, frame = cap.read()
        capture_latency = (time.perf_counter() - start_capture) * 1000  # ms
        
        if not ret:
            print("[-] Failed to grab frame.")
            break
            
        # --- 2. QR Detection & Decoding ---
        start_decode = time.perf_counter()
        data, bbox, _ = detector.detectAndDecode(frame)
        decode_latency = (time.perf_counter() - start_decode) * 1000  # ms
        
        # --- 3. Visualization & Logging ---
        if data:
            total_latency = capture_latency + decode_latency
            print(f"DECODED: {data} | Cam I/O: {capture_latency:.2f}ms | CPU Decode: {decode_latency:.2f}ms | Total Pipeline: {total_latency:.2f}ms")
            
            # Draw a green bounding box around the detected QR code
            if bbox is not None:
                for i in range(len(bbox[0])):
                    pt1 = tuple(map(int, bbox[0][i]))
                    pt2 = tuple(map(int, bbox[0][(i+1) % 4]))
                    cv2.line(frame, pt1, pt2, (0, 255, 0), 3)
        
        # Overlay the decode time on the live video feed
        cv2.putText(frame, f"Decode latency: {decode_latency:.1f}ms", (10, 30), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
                    
        cv2.imshow("QR Scanner Benchmark", frame)
        
        # Break loop on 'q' key press
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
