"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { useRecommendationImpression } from "../hooks/useRecommendationImpression";
import api from "../libs/axios";
import { recordRecommendationEvent } from "../libs/recommendations";
import { useUserStore } from "../store/user";
import ProductImage from "./ProductImage";

type Product = { id: string; name: string; price: number; image_url: string | null };

type Recommendation = {
  recommendation_id: string;
  strategy: "external" | "popularity" | "trending" | "item_item" | "recent";
  products: Product[];
};

function RecommendedProductCard({
  product,
  recommendationId,
  onImpression,
}: {
  product: Product;
  recommendationId?: string;
  onImpression: (recommendationId: string, productId: string) => void;
}) {
  const impressionRef = useRecommendationImpression(
    () => {
      if (recommendationId) onImpression(recommendationId, product.id);
    },
    Boolean(recommendationId),
  );
  const query = recommendationId
    ? new URLSearchParams({ recommendation: recommendationId }).toString()
    : "";
  const href = `/order/${product.id}${query ? `?${query}` : ""}`;

  return (
    <Link
      ref={impressionRef}
      href={href}
      className="surface-card group overflow-hidden transition-transform hover:-translate-y-1"
      onClick={() => {
        if (!recommendationId) return;
        void recordRecommendationEvent({
          eventType: "click",
          productId: product.id,
          recommendationId,
        });
      }}
    >
      <ProductImage
        imageUrl={product.image_url}
        name={product.name}
        className="h-52 w-full transition-transform duration-300 group-hover:scale-[1.03]"
      />
      <span className="flex items-center justify-between gap-4 p-5">
        <strong className="text-lg">{product.name}</strong>
        <span className="font-black text-[var(--brand)]">
          ฿{product.price.toFixed(2)}
        </span>
      </span>
    </Link>
  );
}

export default function RecommendProduct({ fallbackProducts }: { fallbackProducts: Product[] }) {
  const userId = useUserStore((state) => state.id);
  const [recommendation, setRecommendation] = useState<Recommendation | null>(null);
  const recordedImpressions = useRef(new Set<string>());

  const recordImpression = useCallback(
    (recommendationId: string, productId: string) => {
      const attributionKey = `${recommendationId}:${productId}`;
      if (recordedImpressions.current.has(attributionKey)) return;
      recordedImpressions.current.add(attributionKey);
      void recordRecommendationEvent({
        eventType: "impression",
        productId,
        recommendationId,
      });
    },
    [],
  );

  useEffect(() => {
    if (!userId) {
      setRecommendation(null);
      return;
    }
    let active = true;
    api
      .get<Recommendation>("/recommendations", { params: { limit: 3 } })
      .then((response) => {
        if (!active) return;
        setRecommendation(response.data);
      })
      .catch(() => {
        if (active) setRecommendation(null);
      });
    return () => {
      active = false;
    };
  }, [userId]);

  const products = recommendation?.products.length
    ? recommendation.products
    : fallbackProducts;

  return (
    <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
      {products.map((product) => (
        <RecommendedProductCard
          key={`${recommendation?.recommendation_id ?? "fallback"}:${product.id}`}
          product={product}
          recommendationId={recommendation?.recommendation_id}
          onImpression={recordImpression}
        />
      ))}
    </div>
  );
}
