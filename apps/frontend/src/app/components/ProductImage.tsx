type ProductImageProps = {
  imageUrl: string | null;
  name: string;
  className?: string;
};

export default function ProductImage({ imageUrl, name, className = "" }: ProductImageProps) {
  if (imageUrl) {
    return (
      // Product image URLs are supplied by the trusted product catalog.
      // eslint-disable-next-line @next/next/no-img-element
      <img src={imageUrl} alt={name} className={`object-cover ${className}`} />
    );
  }

  return (
    <div
      className={`grid place-items-center bg-gradient-to-br from-[#e8efe9] to-[#f7dfcf] ${className}`}
      role="img"
      aria-label={`ยังไม่มีรูปภาพสำหรับ ${name}`}
    >
      <span className="text-4xl" aria-hidden="true">
        🍽️
      </span>
    </div>
  );
}
