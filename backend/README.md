# Первоначальная backend-часть ЛР2

Материалы предложены в PR #1:
https://github.com/Zergloin/structured-data-from-reports/pull/1

- main.puml - две компонентные модели прежней структуры.
- diagrams/ - определения компонентов API и worker.
- contracts.md - проектные контракты компонентов.
- images/ - ранее полученные изображения.

Исходный коммит cd9e3cddd66d9c0a68f6288bc4e8c40bae703d9a сохранен в истории.
Актуальная часть извлечения находится в src/components/extraction_components.puml
и src/shared/components/. Общий состав API объединяется в
src/components/api_components.puml. Проверки CI относятся к актуальным моделям
из ci/catalog.json; файлы этого каталога backend/ автоматически не проверяются.
