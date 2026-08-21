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
            
        
        # --- 2. QR Detection & Decoding (Multi) ---
        start_decode = time.perf_counter()
        # detectAndDecodeMulti returns: success boolean, list of decoded strings, list of bounding boxes, and list of straight QR codes
        retval, decoded_info, points, _ = detector.detectAndDecodeMulti(frame)
        decode_latency = (time.perf_counter() - start_decode) * 1000  # ms
        
        # --- 3. Visualization & Logging ---
        if retval and len(decoded_info) > 0:
            for i, data in enumerate(decoded_info):
                if data:  # Ensure the string isn't empty (sometimes it detects a shape but fails to decode)
                    total_latency = capture_latency + decode_latency
                    print(f"DECODED [{i+1}]: {data} | Cam I/O: {capture_latency:.2f}ms | CPU Decode: {decode_latency:.2f}ms | Total Pipeline: {total_latency:.2f}ms")
                    
                    # Draw a green bounding box around each detected QR code
                    if points is not None:
                        bbox = points[i]
                        for j in range(len(bbox)):
                            pt1 = tuple(map(int, bbox[j]))
                            pt2 = tuple(map(int, bbox[(j+1) % 4]))
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
