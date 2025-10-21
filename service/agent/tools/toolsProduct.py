
from datetime import datetime
import os
from typing import Any, Dict, List, Optional
import json

from bson import ObjectId
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pymongo import MongoClient
from pymongo.database import Database
from langchain.tools import tool, BaseTool
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain.prompts import ChatPromptTemplate
from langchain_ollama import ChatOllama
from service.product.product import ProductService

class ProductSchema(BaseModel):
    product_name: str
    price: float = Field(..., ge=0)   # must be >= 0
    status: str = "available"
    description: Optional[str] = None
    image: Optional[str] = None
    createAt: datetime = Field(default_factory=datetime.utcnow)
    updateAt: datetime = Field(default_factory=datetime.utcnow)


class LangChainProductService(ProductService):
    """Extended ProductService with LangChain tool integration"""
    
    def __init__(self, uri: str = None):
        load_dotenv()
        self.uri = uri or os.getenv("MONGODB_URI")
        self.client: MongoClient = MongoClient(self.uri)
        self.database: Database = self.client["Products"]
        self.collection = self.database["products"]
        self._tools = None

    # Helper method for JSON serialization
    def _serialize_product(self, product: Dict[str, Any]) -> Dict[str, Any]:
        """Convert MongoDB document for JSON serialization"""
        if product:
            product['_id'] = str(product['_id'])
            for key, value in product.items():
                if isinstance(value, datetime):
                    product[key] = value.isoformat()
        return product

    def _serialize_products(self, products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert list of MongoDB documents for JSON serialization"""
        return [self._serialize_product(product) for product in products]

    # LangChain Tools Integration
    def get_langchain_tools(self) -> List[BaseTool]:
        """Get all LangChain tools for this service"""
        if self._tools is None:
            self._tools = self._create_tools()
        return self._tools

    def _create_tools(self) -> List[BaseTool]:
        """Create LangChain tools from service methods"""
        
        @tool
        def create_product(
            product_name: str,
            price: float,
            status: str = "available",
            description: Optional[str] = None,
            image: Optional[str] = None
        ) -> str:
            """
            Create a new product in the catalog.
            
            Args:
                product_name: Name of the product
                price: Product price (must be >= 0)
                status: Product status (default: available)
                description: Optional product description
                image: Optional image URL
                
            Returns:
                Success message with product ID
            """
            try:
                product_data = {
                    "product_name": product_name,
                    "price": price,
                    "status": status
                }
                if description:
                    product_data["description"] = description
                if image:
                    product_data["image"] = image
                    
                product_id = self.CreateProduct(product_data)
                return f"✅ Product '{product_name}' created successfully with ID: {product_id}"
                
            except Exception as e:
                return f"❌ Error creating product: {str(e)}"

        @tool
        def get_product_by_id(product_id: str) -> str:
            """
            Retrieve product details by ID.
            
            Args:
                product_id: The unique product identifier
                
            Returns:
                Product details or error message
            """
            try:
                product = self.GetProduct(product_id)
                if product:
                    product = self._serialize_product(product)
                    return f"📦 Product Details:\n" + json.dumps(product, indent=2)
                else:
                    return f"❌ Product with ID '{product_id}' not found"
            except Exception as e:
                return f"❌ Error retrieving product: {str(e)}"

        @tool
        def find_product_by_name(product_name: str) -> str:
            """
            Find product by name (exact match).
            
            Args:
                product_name: Name of the product to find
                
            Returns:
                Product details or error message
            """
            try:
                product = self.GetProductByName(product_name)
                if product:
                    product = self._serialize_product(product)
                    return f"📦 Found Product:\n" + json.dumps(product, indent=2)
                else:
                    return f"❌ Product '{product_name}' not found"
            except Exception as e:
                return f"❌ Error finding product: {str(e)}"

        @tool
        def list_all_products() -> str:
            """
            Get list of all products in the catalog.
            
            Returns:
                Formatted list of all products
            """
            try:
                products = self.GetAllProducts()
                if not products:
                    return "📝 No products found in catalog"
                
                products = self._serialize_products(products)
                result = f"📋 **Product Catalog ({len(products)} items):**\n\n"
                
                for product in products:
                    result += f"🏷️ **{product['product_name']}** - ${product['price']:.2f}\n"
                    result += f"   ID: {product['_id']} | Status: {product['status']}\n"
                    if product.get('description'):
                        result += f"   Description: {product['description']}\n"
                    result += "\n"
                
                return result
                
            except Exception as e:
                return f"❌ Error listing products: {str(e)}"

        @tool
        def filter_products_by_status(status: str) -> str:
            """
            Get products filtered by status.
            
            Args:
                status: Status to filter by (e.g., 'available', 'out_of_stock', 'discontinued')
                
            Returns:
                Filtered list of products
            """
            try:
                products = self.GetProductsByStatus(status)
                if not products:
                    return f"📝 No products found with status '{status}'"
                
                products = self._serialize_products(products)
                result = f"📋 **Products with status '{status}' ({len(products)} items):**\n\n"
                
                for product in products:
                    result += f"🏷️ **{product['product_name']}** - ${product['price']:.2f}\n"
                    result += f"   ID: {product['_id']}\n"
                    if product.get('description'):
                        result += f"   Description: {product['description']}\n"
                    result += "\n"
                
                return result
                
            except Exception as e:
                return f"❌ Error filtering products: {str(e)}"

        @tool
        def update_product_price(product_id: str, new_price: float) -> str:
            """
            Update product price.
            
            Args:
                product_id: Product ID to update
                new_price: New price (must be >= 0)
                
            Returns:
                Success message or error
            """
            try:
                if new_price < 0:
                    return "❌ Price cannot be negative"
                    
                result = self.UpdateProductPrice(product_id, new_price)
                if result > 0:
                    return f"✅ Product price updated to ${new_price:.2f}"
                else:
                    return f"❌ Product with ID '{product_id}' not found"
            except Exception as e:
                return f"❌ Error updating price: {str(e)}"

        @tool
        def update_product_status(product_id: str, new_status: str) -> str:
            """
            Update product status.
            
            Args:
                product_id: Product ID to update
                new_status: New status (e.g., 'available', 'out_of_stock', 'discontinued')
                
            Returns:
                Success message or error
            """
            try:
                result = self.UpdateProductStatus(product_id, new_status)
                if result > 0:
                    return f"✅ Product status updated to '{new_status}'"
                else:
                    return f"❌ Product with ID '{product_id}' not found"
            except Exception as e:
                return f"❌ Error updating status: {str(e)}"

        @tool
        def update_product_details(
            product_id: str,
            name: Optional[str] = None,
            description: Optional[str] = None,
            image: Optional[str] = None
        ) -> str:
            """
            Update product details (name, description, or image).
            
            Args:
                product_id: Product ID to update
                name: New product name (optional)
                description: New description (optional)
                image: New image URL (optional)
                
            Returns:
                Success message or error
            """
            try:
                updates = []
                
                if name:
                    result = self.UpdateProductName(product_id, name)
                    if result > 0:
                        updates.append(f"name to '{name}'")
                
                if description:
                    result = self.UpdateProductDescription(product_id, description)
                    if result > 0:
                        updates.append("description")
                
                if image:
                    result = self.UpdateProductImage(product_id, image)
                    if result > 0:
                        updates.append("image")
                
                if updates:
                    return f"✅ Updated product {', '.join(updates)}"
                else:
                    return f"❌ Product with ID '{product_id}' not found or no changes made"
                    
            except Exception as e:
                return f"❌ Error updating product: {str(e)}"

        @tool
        def delete_product(product_id: str) -> str:
            """
            Delete a product from the catalog.
            
            Args:
                product_id: Product ID to delete
                
            Returns:
                Success message or error
            """
            try:
                # First get product details for confirmation
                product = self.GetProduct(product_id)
                if not product:
                    return f"❌ Product with ID '{product_id}' not found"
                
                product_name = product.get('product_name', 'Unknown')
                result = self.DeleteProduct(product_id)
                
                if result > 0:
                    return f"🗑️ Product '{product_name}' (ID: {product_id}) deleted successfully"
                else:
                    return f"❌ Failed to delete product with ID '{product_id}'"
                    
            except Exception as e:
                return f"❌ Error deleting product: {str(e)}"

        @tool
        def search_products(
            keyword: str,
            status_filter: Optional[str] = None,
            min_price: Optional[float] = None,
            max_price: Optional[float] = None
        ) -> str:
            """
            Search products by keyword with optional filters.
            
            Args:
                keyword: Search keyword (searches in name and description)
                status_filter: Optional status filter
                min_price: Optional minimum price filter
                max_price: Optional maximum price filter
                
            Returns:
                Search results
            """
            try:
                # Get all products first
                all_products = self.GetAllProducts()
                
                # Filter by keyword
                keyword_lower = keyword.lower()
                filtered_products = []
                
                for product in all_products:
                    # Search in product name and description
                    name_match = keyword_lower in product.get('product_name', '').lower()
                    desc_match = keyword_lower in product.get('description', '').lower()
                    
                    if name_match or desc_match:
                        filtered_products.append(product)
                
                # Apply additional filters
                if status_filter:
                    filtered_products = [p for p in filtered_products if p.get('status') == status_filter]
                
                if min_price is not None:
                    filtered_products = [p for p in filtered_products if p.get('price', 0) >= min_price]
                
                if max_price is not None:
                    filtered_products = [p for p in filtered_products if p.get('price', 0) <= max_price]
                
                if not filtered_products:
                    return f"🔍 No products found matching '{keyword}' with specified filters"
                
                # Format results
                filtered_products = self._serialize_products(filtered_products)
                result = f"🔍 **Search Results for '{keyword}' ({len(filtered_products)} found):**\n\n"
                
                for product in filtered_products[:10]:  # Limit to 10 results
                    result += f"🏷️ **{product['product_name']}** - ${product['price']:.2f}\n"
                    result += f"   ID: {product['_id']} | Status: {product['status']}\n"
                    if product.get('description'):
                        result += f"   Description: {product['description']}\n"
                    result += "\n"
                
                if len(filtered_products) > 10:
                    result += f"... and {len(filtered_products) - 10} more results"
                
                return result
                
            except Exception as e:
                return f"❌ Error searching products: {str(e)}"

        return [
            create_product,
            get_product_by_id,
            find_product_by_name,
            list_all_products,
            filter_products_by_status,
            update_product_price,
            update_product_status,
            update_product_details,
            delete_product,
            search_products
        ]