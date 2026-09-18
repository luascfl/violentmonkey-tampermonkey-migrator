# Sincronização Automatizada de Scripts (Violentmonkey para Tampermonkey)

Este procedimento automatiza a transferência de todos os seus userscripts do Violentmonkey para o Tampermonkey no Chrome, utilizando Selenium.

## Requisitos
- Python 3
- `pip install python-snappy selenium webdriver-manager`
- Chrome instalado

## Passo a Passo

1. **Certifique-se de que o Chrome está fechado** (evita conflito de perfil).

2. **Execute o Pipeline Completo:**
   O script unificado `exportar_violentmonkey.py` gerencia todo o processo: exportação, migração de formato e sincronização com o navegador.

   ```bash
   cd /home/lucas/Downloads/automacoes/userscripts/violentmonkey-tampermonkey-migrator/
   python3 exportar_violentmonkey.py sync
   ```

## Notas Técnicas
- O script realiza o processo em três etapas: `export` (leitura do IDB), `migrate` (conversão para .user.js) e `sync` (importação no Chrome via Selenium).
- O script utiliza o perfil real do Chrome para carregar o Tampermonkey já instalado.
