import os
import warnings
import pickle
import json
import numpy as np
from dotenv import load_dotenv
from typing import List
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.pydantic_v1 import BaseModel, Field
from langchain_cohere import ChatCohere
from langchain.schema import Document
from typing_extensions import TypedDict
from langgraph.graph import END, StateGraph
import cohere
from pinecone import Pinecone, ServerlessSpec # type: ignore
import time

# Load environment variables from .env file
#load_dotenv('secrets.env')


# Suppress all warnings of type Warning (superclass of all warnings)
warnings.filterwarnings("ignore", category=Warning)
warnings.simplefilter("ignore", LangChainBetaWarning)
import streamlit as st

# Accessing API keys
COHERE_API_KEY = st.secrets["api_keys"]["cohere_api_key"]
LANGCHAIN_ENDPOINT  = st.secrets["api_keys"]["langchain_endpoint"]
LANGCHAIN_API_KEY = st.secrets["api_keys"]["langchain_api_key"]
TAVILY_API_KEY = st.secrets["api_keys"]["tavily_api_key"]
pinecone_api_key = st.secrets["api_keys"]["PINECONE_API_KEY"]

# Accessing settings
LANGCHAIN_TRACING_V2 = st.secrets["settings"]["langchain_tracing_v2"]

# Initialize Pinecone
pinecone_client = Pinecone(api_key=pinecone_api_key)
index_name = "multilingual-climate-change-adaptation-index"
spec = ServerlessSpec(cloud='aws', region='us-east1')

# Check if the index exists, if not, create it
if index_name not in pinecone_client.list_indexes().names():
    pinecone_client.create_index(
        name=index_name,
        dimension=1024,
        metric="dotproduct",
        spec=spec
    )
index = pinecone_client.Index(index_name)

# Suppress all warnings of type Warning (superclass of all warnings)
warnings.filterwarnings("ignore", category=Warning)

# Local Paths
local_folder_path = "./Data"
bm25_model_path = os.path.join(local_folder_path, 'expedition_bm25_encoder_model.pkl')
corpus_path = os.path.join(local_folder_path, 'expedition_corpus.txt')
markdown_docs_path = os.path.join(local_folder_path, 'expedition_markdown_chunked_docs.json')
pdf_files_path = os.path.join(local_folder_path, 'expedition_pdf_names.txt')

# Ensure the files exist
if not all(os.path.exists(path) for path in [bm25_model_path, corpus_path, markdown_docs_path, pdf_files_path]):
    raise FileNotFoundError("One or more required files are missing from the 'Data' directory.")

# Load data from local files
def load_data():
    # Load BM25 model
    with open(bm25_model_path, 'rb') as file:
        bm25 = pickle.load(file)

    # Load markdown chunked documents
    with open(markdown_docs_path) as json_file:
        markdown_chunked_docs = json.load(json_file)

    # Load PDF file names
    with open(pdf_files_path, 'r') as file:
        pdf_files = [line.strip() for line in file]

    return bm25, markdown_chunked_docs, pdf_files

# Load data
bm25, markdown_chunked_docs, pdf_files = load_data()
doc_titles_str = ", ".join(pdf_files)

### Router ###
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.pydantic_v1 import BaseModel, Field
from langchain_cohere import ChatCohere

# Data models
class QuestionVerification(BaseModel):
    f"""Checks if the question is related to climate change topic."""
    query: str = Field(description="The query to evaluate.")

class web_search(BaseModel):
    f"""
    The internet. Use web_search for questions that are related to anything else than {doc_titles_str}.
    """
    query: str = Field(description="The query to use when searching the internet.")

class vectorstore(BaseModel):
    f"""
    A vectorstore containing documents related to {doc_titles_str}.
    Use the vectorstore for questions on these topics.
    """
    query: str = Field(description="The query to use when searching the vectorstore.")

# Preamble
preamble = f"""
You are an intelligent assistant trained to first evaluate if a user's question is saying hi or introducing themselves then if the content relates in any way to climate change topics or the environment and if not, do not answer.
If the question is related to climate change, decide whether to use the vectorstore containing documents on {doc_titles_str}, or to route climate change topics or global warming question to general web search.
"""

# Set up the LLM with the ability to make routing decisions
llm = ChatCohere(model="command-r-plus", temperature=0)
structured_llm_router = llm.bind_tools(tools=[QuestionVerification, web_search, vectorstore], preamble=preamble)

# Define a prompt that asks the LLM to make a routing decision
route_prompt = ChatPromptTemplate.from_messages([
    ("system", preamble),
    ("human", "{question}"),
    ("system", "Based on the question content, should this query be directed to the vectorstore for climate-specific documents, or should it be searched on the web for other information?")
])

# Combine the route prompt with the LLM routing logic
question_router = route_prompt | structured_llm_router

### Retrieval Grader ###

# Data model
class GradeDocuments(BaseModel):
    """Binary score for relevance check on retrieved documents."""
    binary_score: str = Field(description="Documents are relevant to the question, 'yes' or 'no'")

# Prompt
preamble = """You are a grader assessing relevance of a retrieved document to a user question. \n
If the document contains keyword(s) or semantic meaning related to the user question, grade it as relevant. \n
Give a binary score 'yes' or 'no' score to indicate whether the document is relevant to the question."""

# LLM with function call
llm = ChatCohere(model="command-r-plus", temperature=0)
structured_llm_grader = llm.with_structured_output(GradeDocuments, preamble=preamble)

grade_prompt = ChatPromptTemplate.from_messages(
    [
        ("human", "Retrieved document: \n\n {document} \n\n User question: {question}"),
    ]
)

retrieval_grader = grade_prompt | structured_llm_grader

### Generate Response ###
from langchain_core.output_parsers import StrOutputParser
from langchain_core.messages import HumanMessage

# Preamble
preamble="""
You are an expert in climate change and global warming. You will be answering questions from a broad audience that includes high school students and professionals. You should adopt the persona of an educator, providing information that is both accessible and engaging.

Persona:
Consider yourself an educator for both youth and adults.
Ensure your responses are helpful, harmless, and honest.

Language:
Easy to read and understand for grade 9 students.

Tone and Style:
Friendly and approachable
Free of jargon
Factual and accurate

Content Requirements:
Detailed and complete responses
Use bullet points for clarity
Provide intuitive examples when possible

Leverage Constitutional AI:
Align your responses with human values.
Ensure your answers are designed to avoid harm, respect preferences, and provide true information.

Example Question: What is climate change and why should we care?
Response:
Let's talk about climate change and why it matters to all of us.

**What is Climate Change?**

- **Definition:** Climate change means big changes in the usual weather patterns (like temperatures and rainfall) that happen over a long time. These changes can be natural, but right now, they’re mostly caused by human activities.
- **Key Factors:**

  - **Greenhouse Gases (GHGs):** When we burn fossil fuels (like coal, oil, and natural gas) for energy, it releases gases that trap heat in the atmosphere.

  - **Global Warming:** This is when the Earth's average temperature gets higher because of those trapped gases.

**Why Should We Care?**

- **Impact on Weather:**

  - **Extreme Weather Events:** More frequent and intense heatwaves, hurricanes, and heavy rainstorms can lead to serious damage and danger.
  - **Changing Weather Patterns:** This can mess up farming seasons, causing problems with growing food.

- **Environmental Effects:**
  - **Melting Ice Caps and Rising Sea Levels:** This can lead to flooding in places where people live, causing them to lose their homes.
  - **Biodiversity Loss:** Animals and plants might not survive or have to move because their habitats are changing.

- **Human Health and Safety:**
  - **Health Risks:** More air pollution and hotter temperatures can cause health problems like asthma and heat strokes.
  - **Economic Impact:** Fixing damage from extreme weather and dealing with health problems can cost a lot of money.

**What Can We Do to Help?**

- **Reduce Carbon Footprint:**

  - **Energy Efficiency:** Use devices that save energy, like LED bulbs and efficient appliances.
  - **Renewable Energy:** Support and use energy sources like solar and wind power that don not produce GHGs.

- **Adopt Sustainable Practices:**

  - **Reduce, Reuse, Recycle:** Cut down on waste by following these three steps.
  - **Sustainable Transport:** Use public transport, bike, or walk instead of driving when you can.
**Why Your Actions Matter:**

- **Collective Impact:** When lots of people make small changes, it adds up to a big positive effect on our planet.
- **Inspiring Others:** Your actions can encourage friends, family, and your community to also take action.
**Let's Make a Difference Together!**

  - **Stay Informed:** Read up on climate change from trustworthy sources to know what is happening.
  - **Get Involved:** Join local or online groups that work on climate action.

**Questions or Curious About Something?**

Feel free to ask any questions or share your thoughts. We are all in this together, and every little bit helps!
"""

# LLM
llm = ChatCohere(model_name="command-r-plus", temperature=0).bind(preamble=preamble)

# Prompt
prompt = lambda x: ChatPromptTemplate.from_messages(
    [
        HumanMessage(
            f"Question: {x['question']} \nAnswer: ",
            additional_kwargs={"documents": x["documents"]},
        )
    ]
)

# Chain
rag_chain = prompt | llm | StrOutputParser()

### LLM Fallback ###

# Preamble
preamble = """You are an assistant for question-answering tasks. Answer the question based upon your knowledge. Use three sentences maximum and keep the answer concise."""

# LLM
llm = ChatCohere(model_name="command-r-plus", temperature=0).bind(preamble=preamble)

# Prompt
prompt = lambda x: ChatPromptTemplate.from_messages(
    [
        HumanMessage(
            f"Question: {x['question']} \nAnswer: "
        )
    ]
)

# Chain
llm_chain = prompt | llm | StrOutputParser()

### Hallucination Grader ###

# Data model
class GradeHallucinations(BaseModel):
    """Binary score for hallucination present in generation answer."""

    binary_score: str = Field(description="Answer is grounded in the facts, 'yes' or 'no'")

# Preamble
preamble = """You are a grader assessing whether an LLM generation is grounded in / supported by a set of retrieved facts. \n
Give a binary score 'yes' or 'no'. 'Yes' means that the answer is grounded in / supported by the set of facts."""

# LLM with function call
llm = ChatCohere(model="command-r-plus", temperature=0)
structured_llm_grader = llm.with_structured_output(GradeHallucinations, preamble=preamble)

# Prompt
hallucination_prompt = ChatPromptTemplate.from_messages(
    [
        # ("system", system),
        ("human", "Set of facts: \n\n {documents} \n\n LLM generation: {generation}"),
    ]
)

hallucination_grader = hallucination_prompt | structured_llm_grader

### Answer Grader ###
# Data model
class GradeAnswer(BaseModel):
    """Binary score to assess answer addresses question."""

    binary_score: str = Field(description="Answer addresses the question, 'yes' or 'no'")

# Preamble
preamble = """You are a grader assessing whether an answer addresses / resolves a question \n
Give a binary score 'yes' or 'no'. Yes' means that the answer resolves the question."""

# LLM with function call
llm = ChatCohere(model="command-r-plus", temperature=0)
structured_llm_grader = llm.with_structured_output(GradeAnswer, preamble=preamble)

# Prompt
answer_prompt = ChatPromptTemplate.from_messages(
    [
        ("human", "User question: \n\n {question} \n\n LLM generation: {generation}"),
    ]
)

answer_grader = answer_prompt | structured_llm_grader

### Search ###
from langchain_community.tools.tavily_search import TavilySearchResults
web_search_tool = TavilySearchResults()

### Graph ###
from typing_extensions import TypedDict
from typing import List

class GraphState(TypedDict):
    """|
    Represents the state of our graph.

    Attributes:
        question: question
        generation: LLM generation
        documents: list of documents
        citations: sources
    """
    question : str
    generation : str
    documents : List[str]
    citations: str

from langchain.schema import Document

def retrieve(state):
    """
    Retrieve documents
    Args:
        state (dict): The current graph state
    Returns:
        state (dict): New key added to state, documents, that contains retrieved documents
    """
    question = state["question"]
    reranked_docs = rerank_fcn(question, bm25, alpha=0.3, top_k=3)    
    documented_docs = [
    Document(
        page_content=doc['section_text'] if len(cohere_client.tokenize(text=doc['section_text'], model="command-r-plus").tokens) < 500 else doc['summary_text'],
        metadata={'filename': doc['header']}
    )
    for doc in reranked_docs
    ]
    return {"documents": documented_docs, "question": question}
    
def llm_fallback(state):
    """
    Generate answer using the LLM w/o vectorstore
    Args:
        state (dict): The current graph state
    Returns:
        state (dict): New key added to state, generation, that contains LLM generation
    """
    question = state["question"]
    generation = llm_chain.invoke({"question": question})
    return {"question": question, "generation": generation}

def rag(state):
    """
    Generate answer using the vectorstore
    Args:
        state (dict): The current graph state
    Returns:
        state (dict): New key added to state, generation, that contains LLM generation
    """
    question = state["question"]
    documents = state["documents"]
    citations = state["citations"]

    if not isinstance(documents, list):
      documents = [documents]
    # RAG generation
    generation = rag_chain.invoke({"documents": documents, "question": question})
    return {"documents": documents, "citations":citations, "question": question, "generation": generation}

def grade_documents(state):
    """
    Determines whether the retrieved documents are relevant to the question.
    Args:
        state (dict): The current graph state
    Returns:
        state (dict): Updates documents key with only filtered relevant documents
    """
    #print("---CHECK DOCUMENT RELEVANCE TO QUESTION---")
    question = state["question"]
    documents = state["documents"]
    # Score each doc
    filtered_docs = []
    for d in documents:
        for _ in range(2):        
            try:
                score = retrieval_grader.invoke({"question": question, "document": d.page_content})
                grade = score.binary_score
                if grade == "yes":
                    filtered_docs.append(d)
                break
            except Exception as e:
                print(f'Error getting retrieval grader score: {e}')
                time.sleep(2)
    citations = "  \n".join(set(doc.metadata.get('filename') for doc in filtered_docs))
    return {"documents": filtered_docs, "citations": citations, "question": question}

def verify_question(state):
    """
    Verify if the question is related to climate change or global warming topics, or a welcome or introduction.
    Args:
        state (dict): The current graph state
    Returns:
        str: Next node to call based on the verification result, or "not_related" if the question is not related to climate change
    """
    #print("---VERIFY QUESTION---")
    question = state["question"]
    # If the question is a personal question directly asking the LLM, route it to the LLM for generating a response
    if question.lower().startswith(("hello", "hi", "how are you", "are you okay", "how's your day")):
        #print("---PERSONAL QUESTION, ROUTE TO LLM---")
        return "llm_fallback"

    verification = question_router.invoke({"question": question})

    if "tool_calls" in verification.response_metadata and len(verification.response_metadata["tool_calls"]) > 0:
        #print("---QUESTION IS RELATED TO CLIMATE CHANGE, ROUTE TO VECTORSTORE---")
        return "retrieve"
    else:
        #print("---QUESTION IS NOT RELATED TO CLIMATE CHANGE---")
        return "not_related"

def not_related_response(state):
    """
    Respond to questions not related to climate change.
    Args:
        state (dict): The current graph state
    Returns:
        str: A message indicating that the question is not related to climate change
    """
    question = state["question"]
    generation = "Sorry, this question is not related to climate change. Do you have any questions related to the topic?"
    return {"question": question, "generation": generation}

def web_search(state):
    """
    Web search based on the re-phrased question.
    Args:
        state (dict): The current graph state
    Returns:
        state (dict): Updates documents key with appended web results
    """
    #print("---WEB SEARCH---")
    question = state["question"]
    # Web search
    docs = web_search_tool.invoke({"query": question})
    web_results = "\n".join([d["content"] for d in docs])
    web_results = Document(page_content=web_results)
    citations = "  \n".join([d["url"] for d in docs])
    return {"documents": web_results, "citations": citations, "question": question}

# Hybrid Search and Re-ranking
def get_dense_embeddings_query(query):
    try:
        response = cohere_client.embed(
            model='embed-multilingual-v3.0',
            input_type="search_query",
            texts=[query],
            truncate='END'
        )
        return np.asarray(response.embeddings)
    except Exception as e:
        print(f"Error getting embeddings: {str(e)}")
        return None

def weight_by_alpha(sparse_embedding, dense_embedding, alpha):
    # alpha=0: pure keyword search (sparse embedding)
    # alpha=1: pure semantic search (dense embedding)
    if alpha < 0 or alpha > 1:
        raise ValueError("Alpha must be between 0 and 1")
    hsparse = {
        'indices': sparse_embedding['indices'],
        'values': [v * (1 - alpha) for v in sparse_embedding['values']]
    }
    hdense = [v * alpha for v in dense_embedding]
    return hsparse, hdense

def get_hybrid_results(query, bm25, alpha, top_k):
    query_sparse_embedding = bm25.encode_queries(query)
    query_dense_embedding = get_dense_embeddings_query(query)
    try:
        scaled_sparse, scaled_dense = weight_by_alpha(query_sparse_embedding, query_dense_embedding[0], alpha)
        hybrid = index.query(
            vector=scaled_dense,
            sparse_vector=scaled_sparse,
            top_k=top_k,
            include_metadata=True
        )
        return hybrid
    except Exception as e:
        print(f"Error querying index: {str(e)}")
        return None

def rerank_fcn(query, bm25, alpha, top_k):
    try:
        hybrid = get_hybrid_results(query, bm25, alpha, top_k)
        if hybrid is None or "matches" not in hybrid:
            print("Error: No matches found in hybrid results.")
            return []

        docs_to_rerank = [doc.get('metadata') for doc in hybrid.get("matches")]
    
        # Filter out documents with only empty or whitespace-only fields
        valid_docs_to_rerank = []
        for doc in docs_to_rerank:
            if doc:
                valid_doc = {field: value for field, value in doc.items() if field != 'order_id' and value and value.strip()}
                if valid_doc:
                    valid_docs_to_rerank.append(valid_doc)

        if not valid_docs_to_rerank:
            print("No valid documents to rerank.")
            return []

        # Prepare the documents in the expected format for Cohere Rerank API
        formatted_docs = [{"text": " ".join(doc.values())} for doc in valid_docs_to_rerank]

        rerank_results = cohere_client.rerank(
            query=query,
            documents=formatted_docs,
            top_n=top_k,
            model="rerank-multilingual-v3.0"
        )

        docs_retrieved = [valid_docs_to_rerank[doc.index] for doc in rerank_results.results]
        
        # Eliminate duplicate docs
        unique_docs_retrieved = []
        seen = set()
        for doc in docs_retrieved:
            tup = tuple(doc.items())
            if tup not in seen:
                seen.add(tup)
                unique_docs_retrieved.append(doc)

        # Get section text
        for doc in unique_docs_retrieved:
            section_splits_list = markdown_chunked_docs[doc['header']][doc['section_header']]
            section_text = ' '.join([split[1] for split in section_splits_list])
            doc['section_text'] = section_text
        
        return unique_docs_retrieved
    
    except Exception as e:
        print(f"Error in reranking: {str(e)}")
        return []
    
### Edges ###
def route_question(state):
    """
    Route question based on the verification result.
    Args:
        state (dict): The current graph state
    Returns:
        str: Next node to call, or None if the question is not related to climate change
    """
    #print("---ROUTE QUESTION---")
    next_node = verify_question(state)
    if next_node is None:
        return END
    return next_node

def decide_to_generate(state):
    """
    Determines whether to generate an answer, or re-generate a question.
    Args:
        state (dict): The current graph state
    Returns:
        str: Binary decision for next node to call
    """
    #print("---ASSESS GRADED DOCUMENTS---")
    filtered_documents = state["documents"]

    if filtered_documents and len(filtered_documents)>0:
        return "rag"
    else:
        return "web_search"

def grade_generation_v_documents_and_question(state):
    """
    Determines whether the generation is grounded in the document and answers the question.
    Args:
        state (dict): The current graph state
    Returns:
        str: Decision for next node to call or the generation if hallucination grading fails
    """
    #print("---CHECK HALLUCINATIONS---")
    question = state["question"]
    documents = state["documents"]
    generation = state["generation"]
    
    score = answer_grader.invoke({"question": question, "generation": generation})
    grade = score.binary_score
    if grade == "yes":
        return "useful"
    else:
        return "not useful"
        
    #score = hallucination_grader.invoke({"documents": documents, "generation": generation})
    #if score is None:
    #    print("Hallucination grading failed to return a result. Showing unverified generation.")
    #    return generation  # Returning the generation directly if hallucination check fails

    #grade = score.binary_score

    # Check hallucination
    #if grade == "yes":
        #print("---DECISION: GENERATION IS GROUNDED IN DOCUMENTS---")
        # Check question-answering
        #print("---GRADE GENERATION vs QUESTION---")
    #    score = answer_grader.invoke({"question": question, "generation": generation})
    #    grade = score.binary_score
    #    if grade == "yes":
            #print("---DECISION: GENERATION ADDRESSES QUESTION---")
    #        return "useful"
    #    else:
            #print("---DECISION: GENERATION DOES NOT ADDRESS QUESTION---")
    #        return "not useful"
    #else:
        #pprint.pprint("---DECISION: GENERATION IS NOT GROUNDED IN DOCUMENTS, RE-TRY---")
    #    return "not supported"

def generate(state):
    question = state["question"]
    documents = state["documents"]
    if not isinstance(documents, list):
        documents = [documents]
    generation = state['generation']
    citations = state['citations']

    return {"documents": documents, "citations":citations, "question": question, "generation": generation}

workflow = StateGraph(GraphState)

# Define the nodes
workflow.add_node("web_search", web_search) # web search
workflow.add_node("retrieve", retrieve) # retrieve
workflow.add_node("grade_documents", grade_documents) # grade documents
workflow.add_node("rag", rag) # rag
workflow.add_node("llm_fallback", llm_fallback) # llm
workflow.add_node("not_related_response", not_related_response) # not related
workflow.add_node("generate", generate) 

# Build graph
workflow.set_conditional_entry_point(
    route_question,
    {
        "retrieve": "retrieve",
        "not_related": "not_related_response",
        "llm_fallback": "llm_fallback",
    },
)

workflow.add_edge("llm_fallback", "generate")
workflow.add_edge("not_related_response", "generate")
workflow.add_edge("web_search", "rag")
workflow.add_edge("retrieve", "grade_documents")

workflow.add_conditional_edges(
    "grade_documents",
    decide_to_generate,
    {
        "web_search": "web_search",
        "rag": "rag",
    },
)
workflow.add_conditional_edges(
    "rag",
    grade_generation_v_documents_and_question,
    {
        #"not supported": "web_search", # Hallucinations: re-generate
        "not useful": "web_search", # Fails to answer question: fall-back to web-search
        "useful": "generate"
    },
)
workflow.add_edge("generate", END)

# Compile
app = workflow.compile()

def run_workflow(input_state):
    result = app.invoke(input_state)
    return result
