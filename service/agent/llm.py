from langchain.prompts import ChatPromptTemplate
from langchain_ollama import ChatOllama
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain.memory import ConversationBufferWindowMemory
from langchain_core.messages import HumanMessage, AIMessage
from langchain_openai import ChatOpenAI, OpenAI
from service.agent.tools.recommender import LangChainRecommendationService
from service.agent.tools.toolsOrder import LangChainOrderService
from service.agent.tools.toolsProduct import LangChainProductService
from service.agent.tools.toolsUser import LangChainUsers

class FoodOrderingAgentWithUserMemory:
    def __init__(self):
        # self.llm = ChatOllama(
        #     base_url="http://localhost:11434",
        #     model="mistral-nemo",
        #     validate_model_on_init=True,
        #     temperature=0,
        # )
        
        self.llm = ChatOpenAI(
            model="gpt-5",
            temperature=0,
            streaming=False,
            callbacks=[]
        )
        
        # เก็บ memory แยกตาม user_id
        self.user_memories = {}
        
        self.memory = ConversationBufferWindowMemory(
            k=10,  # เก็บ 10 รอบการสนทนาล่าสุด
            memory_key="chat_history",
            return_messages=True,
            input_key="message",
            output_key="output"
        )
        
        # ออกแบบ Prompt (ปรับปรุงให้รองรับ memory)
        self.prompt_template = ChatPromptTemplate.from_messages([
            ("system",
             "You are an intelligent food ordering assistant for a restaurant platform. "
             "Your role is to help users and staff manage orders, menu items, and delivery.\n"
             "If user add addon, Additional items cost 10 baht each.\n"
             "If food order doesn't appaer in product database. get all order and find similarity product instead\n"
             "If it is a description such as less spicy, more spicy, less rice, more rice, don't want more cucumber, these are descriptions. But if adding fried eggs, adding pork, adding chicken is considered an addon.\n"
             "You should anwser with Thai language because customer base are from Thailand.\n\n"
             
             
             "**Capabilities:**\n"
             "1. Take new orders from customers\n"
             "2. Retrieve order details by order ID\n"
             "3. List all orders for a user\n"
             "4. Update order status (e.g., pending, preparing, delivered, canceled)\n"
             "5. Cancel orders when requested\n"
             "6. Get the most recent order for a user\n"
             "7. Retrieve orders within a date range\n"
             "8. Handle product information and availability\n\n"
             
             "**Memory Context:**\n"
             "- Remember previous conversations and orders discussed\n"
             "- Reference past interactions when relevant\n"
             "- Maintain context across multiple requests\n"
             "- If user refers to 'my order' or 'the order', use conversation history to understand which order\n\n"
             
             "**Security & Privacy:**\n"
             "- Never expose sensitive customer information\n"
             "- Validate product availability before creating an order\n"
             "- Confirm destructive actions (canceling orders) with the user\n"
             "- Handle JSON input (addon, options) safely\n\n"
             
             "**Step-by-Step Reasoning:**\n"
             "Always follow these steps when answering:\n"
             "1. Understand: Determine what the user wants (check conversation history for context)\n"
             "2. Validate: Ensure the requested action is valid\n"
             "3. Plan: Decide which tool(s) to use\n"
             "4. Execute: Perform the action using the tool(s)\n"
             "5. Verify: Check that the results make sense\n"
             "6. Respond: Provide clear feedback to the user\n\n"
             
             "**Communication Style:**\n"
             "- Be friendly, professional, and helpful\n"
             "- Explain your steps clearly (CoT)\n"
             "- Use emojis to improve UX when appropriate\n"
             "- Ask for confirmation for destructive operations (e.g., canceling orders)\n"
             "- Reference previous conversations when appropriate\n\n"
             
             "**Common Scenarios:**\n"
             "- Customer places a new order with multiple items and addon\n"
             "- Staff checks the status of a specific order\n"
             "- Retrieve all orders for a customer\n"
             "- Cancel an order due to unavailability\n"
             "- List orders within a specific date range\n"
             "- Update order status as preparation progresses\n\n"
             "Use the descriptions of the tools to select the correct one for each task."),
            
            ("system", "Example 1: \n"),
            ("human", "I want to order a Margherita Pizza with extra cheese"),
            ("ai",
             "I will create a new order 🍕🧀\n\n"
             "**Reasoning:**\n"
             "1. Understand: Customer wants a Margherita Pizza with extra cheese\n"
             "2. Validate: Check product availability\n"
             "3. Plan: Use `create_order` tool with user ID, product, price, and addon\n"
             "4. Execute: Create the order\n"
             "5. Verify: Confirm the order was created successfully\n"
             "6. Respond: Provide confirmation to the user\n"
             
             "No need to show reasoning to user this is just a demonstration how you should reasoning \n"
             ),
            ("system", "Example 2: \n"),
            
            ("human", "Show me the latest order for user123"),
            ("ai",
             "I will fetch the most recent order 🕒\n\n"
             "**Reasoning:**\n"
             "1. Understand: User wants the latest order\n"
             "2. Plan: Use `get_latest_user_order` tool\n"
             "3. Execute: Retrieve the order\n"
             "4. Verify: Ensure order details are correct\n"
             "5. Respond: Provide the order info to the user\n"
             
             "No need to show reasoning to user this is just a demonstration how you should reasoning \n"
             ),
            
            ("system", "Example 3: \n"),
            ("human", "Cancel order 6523abcd"),
            ("ai",
             "I will cancel the order ❌\n\n"
             "**Reasoning:**\n"
             "1. Understand: Customer wants to cancel a specific order\n"
             "2. Validate: Check if order exists and is cancellable\n"
             "3. Plan: Use `cancel_order` tool\n"
             "4. Execute: Cancel the order\n"
             "5. Verify: Confirm cancellation\n"
             "6. Respond: Provide success or failure message to the user\n"
             
             "No need to show reasoning to user this is just a demonstration how you should reasoning \n"
             ),
            
            # Memory placeholder - LangChain จะใส่ chat history ตรงนี้
            ("placeholder", "{chat_history}"),
            ("user", "{message}"),
            ("placeholder", "{agent_scratchpad}"),
            ("system", "Customer profile (JSON, may be empty): {user_info}"),
        ])
        
        # สร้าง service instances
        self.order_service = LangChainOrderService()
        self.product_service = LangChainProductService()
        self.user_service = LangChainUsers()
        self.recommendation_service = LangChainRecommendationService(api_base_url="http://127.0.0.1:8080")
        
        # Get tools
        self.tools = (
            self.order_service.get_langchain_tools() +
            self.product_service.get_langchain_tools() +
            self.user_service.get_langchain_tools() + 
            self.recommendation_service.get_langchain_tools()
        )
        
        # Build the agent
        self.agent = create_tool_calling_agent(self.llm, self.tools, self.prompt_template)
        
        # Wrap it in an executor with memory
        self.agent_executor = AgentExecutor(
            agent=self.agent,
            tools=self.tools,
            memory=self.memory,
            verbose=True,
            max_iterations=5,
            early_stopping_method="force",
            return_intermediate_steps=True
        )
        # ... (setup เดียวกัน)
    
    def get_or_create_memory(self, user_id: str):
        """สร้างหรือดึง memory สำหรับผู้ใช้คนนี้"""
        if user_id not in self.user_memories:
            self.user_memories[user_id] = ConversationBufferWindowMemory(
                k=10,
                memory_key="chat_history",
                return_messages=True,
                input_key="message",
                output_key="output"
            )
        return self.user_memories[user_id]
    
    def chat(self, message: str, user_id: str, user_info: str = "{}"):
        if not message or not message.strip():
            return "⚠️ คุณต้องพิมพ์ข้อความก่อนครับ"

        memory = self.get_or_create_memory(user_id)

        agent_executor = AgentExecutor(
            agent=self.agent,
            tools=self.tools,
            memory=memory,
            verbose=True,
            max_iterations=10,
            early_stopping_method="force",
            return_intermediate_steps=True
        )

        try:
            response = agent_executor.invoke({
                "message": message.strip(),
                "user_info": user_info
            })
            return response["output"]
        except Exception as e:
            return f"เกิดข้อผิดพลาด: {str(e)}"

# สร้าง instance ของ agent
agent_executor = FoodOrderingAgentWithUserMemory()