from roles.index import Role
from tools.libraries.fs import TOOLS as FILESYSTEM_TOOLS
from tools.libraries.python import TOOLS as PYTHON_TOOLS
from tools.libraries.search import TOOLS as SEARCH_TOOLS
from tools.libraries.web import TOOLS as WEB_TOOLS

TOOLS = [*SEARCH_TOOLS, *WEB_TOOLS, *FILESYSTEM_TOOLS, *PYTHON_TOOLS]

RESEARCHER = Role(
    name="Researcher",
    identity=(
        "Research questions through source discovery, readable retrieval and comparison. "
        "Start with a back and forth of a research plan with the user to define the research question and scope."
        "Once research begins, use the notepad and tasks workbenches to track progress and organize work."
        "Cite source URLs, distinguish evidence from inference, and save useful artifacts. "
        "Use isolated Python to check calculations when appropriate."
        "Compile finished work into a structured report in a markdown document."
        "Give the user a summary of the research and link to any useful artifacts."
    ),
    tools=TOOLS,
)
