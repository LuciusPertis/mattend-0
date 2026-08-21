import qrcode

def generate_test_qr(data="STUDENT_UUID_123456789_TIMESTAMP", filename="test_qr.png"):
    # Using low error correction (ERROR_CORRECT_L) as screens are highly legible
    # This reduces matrix density and speeds up decoding on the lab CPU.
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=15,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    img.save(filename)
    print(f"[+] Generated benchmark QR code saved as {filename}")

if __name__ == "__main__":
    generate_test_qr()
