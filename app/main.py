"""Point d'entrée Streamlit de l'agent de tri des emails."""

import streamlit as st

st.set_page_config(page_title="Agent emails", page_icon="📬", layout="wide")

page = st.navigation(
    {
        "": [
            st.Page("app_pages/boite.py", title="Boîte de réception", icon=":material/inbox:", default=True),
            st.Page("app_pages/tableau_de_bord.py", title="Tableau de bord", icon=":material/dashboard:"),
        ],
        "Journal": [
            st.Page("app_pages/journal_valides.py", title="Brouillons validés", icon=":material/check_circle:"),
            st.Page("app_pages/journal_ignores.py", title="Ignorés", icon=":material/do_not_disturb_on:"),
            st.Page("app_pages/journal_historique.py", title="Historique", icon=":material/history:"),
        ],
    },
    position="top",
)

st.title("Agent emails")
st.caption("Tri, extraction et brouillons de réponse. Rien n'est envoyé sans votre validation.")
st.header(page.title)
page.run()
