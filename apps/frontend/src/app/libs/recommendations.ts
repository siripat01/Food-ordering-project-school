import api from "./axios";

export type RecommendationEventType = "impression" | "click" | "add_to_cart";

export async function recordRecommendationEvent({
  eventType,
  productId,
  recommendationId,
}: {
  eventType: RecommendationEventType;
  productId: string;
  recommendationId: string;
}) {
  try {
    await api.post("/recommendations/events", {
      event_type: eventType,
      product_id: productId,
      recommendation_id: recommendationId,
    });
  } catch {
    // Recommendation analytics are best-effort and must never interrupt ordering.
  }
}

export function recordRecommendationOrderIntent({
  productId,
  recommendationId,
}: {
  productId: string;
  recommendationId: string;
}) {
  // The current API calls this lower-value conversion `add_to_cart`. A trusted
  // purchase signal is recorded by the backend only after order completion.
  return recordRecommendationEvent({
    eventType: "add_to_cart",
    productId,
    recommendationId,
  });
}
