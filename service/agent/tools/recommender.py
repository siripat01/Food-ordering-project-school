from typing import List, Dict, Any
from langchain.tools import tool
from langchain.tools import BaseTool
import requests

class LangChainRecommendationService:
    """Recommendation Service with LangChain tool integration"""

    def __init__(self, api_base_url: str):
        self.api_base_url = api_base_url
        self._tools = None

    def get_langchain_tools(self) -> List[BaseTool]:
        """Get all LangChain tools for this service"""
        if self._tools is None:
            self._tools = self._create_tools()
        return self._tools

    def _create_tools(self) -> List[BaseTool]:
        """Create LangChain tools from service methods"""

        @tool
        def get_collaborative_recommendations(user_id: str, n_recommendations: int = 5) -> List[Dict[str, Any]]:
            """Get collaborative filtering recommendations for a user"""
            try:
                url = f"{self.api_base_url}/recommendations/{user_id}?n_recommendations={n_recommendations}"
                response = requests.get(url, timeout=5)
                response.raise_for_status()
                results = response.json()
                # Ensure JSON-serializable
                output = []
                
                for r in results:
                    r['score'] = float(r.get('score', 0))
                    output.append(r['item_name'])    
                return output
            except requests.exceptions.RequestException as e:
                print(f"Error in get_collaborative_recommendations: {e}")
                return []

        @tool
        def get_trending_items(n_recommendations: int = 5) -> List[Dict[str, Any]]:
            """Get currently trending items
                n_recommendations: int = 5
            """
            try:
                url = f"{self.api_base_url}/trending?n_recommendations={n_recommendations}"
                response = requests.get(url, timeout=5)
                response.raise_for_status()
                results = response.json()
                
                output=[]
                
                for r in results:
                    output.append(r['item_name'])    
                return output
            except requests.exceptions.RequestException as e:
                print(f"Error in get_trending_items: {e}")
                return []

        return [
            get_collaborative_recommendations,
            get_trending_items
        ]
