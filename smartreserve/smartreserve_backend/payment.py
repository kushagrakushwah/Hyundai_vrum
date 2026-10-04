import os

try:
    import razorpay
except ImportError:
    razorpay = None

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "rzp_test_placeholder")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "placeholder")
ESCROW_AMOUNT_RS = 200  # ₹200 deposit hold
UPI_ID = "8109703612@sbi"
UPI_NAME = "Kushagra Kushwah"

class PaymentEngine:
    def __init__(self):
        self.mock_mode = False
        self.UPI_ID = UPI_ID
        self.UPI_NAME = UPI_NAME
        if not razorpay or RAZORPAY_KEY_ID == "rzp_test_placeholder":
            self.mock_mode = True
        else:
            try:
                self.client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
            except Exception:
                self.mock_mode = True

                
    def create_escrow_order(self, reservation_id: str, user_id: str, amount_rs: int = ESCROW_AMOUNT_RS) -> dict:
        if self.mock_mode:
            return {
                "order_id": f"mock_order_{reservation_id}",
                "amount": amount_rs * 100,
                "currency": "INR",
                "mock": True,
                "upi_id": UPI_ID,
                "status": "created"
            }
        
        try:
            data = {
                "amount": amount_rs * 100,
                "currency": "INR",
                "receipt": f"receipt_{reservation_id}",
                "notes": {
                    "reservation_id": reservation_id,
                    "user_id": user_id
                }
            }
            order = self.client.order.create(data=data)
            return order
        except Exception as e:
            # Fallback to mock if API call fails
            return {
                "order_id": f"mock_order_{reservation_id}",
                "amount": amount_rs * 100,
                "currency": "INR",
                "mock": True,
                "error": str(e),
                "upi_id": UPI_ID,
                "status": "created"
            }
            
    def capture_no_show_fee(self, order_id: str, payment_id: str) -> dict:
        if self.mock_mode:
            return {"captured": False, "mock": True, "reason": "No real payment in demo mode"}
        # Real implementation would capture via self.client.payment.capture(...)
        return {"captured": True, "mock": False, "status": "captured"}
        
    def full_refund(self, payment_id: str) -> dict:
        if self.mock_mode:
            return {"refunded": False, "mock": True, "reason": "No real payment in demo mode"}
        # Real implementation would refund via self.client.payment.refund(...)
        return {"refunded": True, "mock": False, "status": "refunded"}
        
    def verify_payment_signature(self, order_id: str, payment_id: str, signature: str) -> bool:
        if self.mock_mode:
            return True
        try:
            params_dict = {
                'razorpay_order_id': order_id,
                'razorpay_payment_id': payment_id,
                'razorpay_signature': signature
            }
            self.client.utility.verify_payment_signature(params_dict)
            return True
        except Exception:
            return False
