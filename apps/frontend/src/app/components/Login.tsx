import { login } from "../libs/login";

export default function Login({ className = "" }: { className?: string }) {
  return (
    <button type="button" onClick={login} className={`primary-button ${className}`}>
      เข้าสู่ระบบด้วย LINE
    </button>
  );
}
