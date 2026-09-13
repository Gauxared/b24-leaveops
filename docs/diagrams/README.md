# Диаграммы

## Структура

- `src/*.bpmn` — канонические BPMN 2.0 XML collaboration;
- `src/*.puml` — UML и C4 исходники PlantUML;
- `rendered/*.svg` — готовые изображения для GitHub.

Сгенерированный SVG никогда не является единственным экземпляром модели: рядом всегда хранится редактируемый текстовый исходник.

## Нотации

| Артефакт | Нотация | Исходник |
| --- | --- | --- |
| AS-IS / TO-BE | BPMN 2.0 Collaboration | `as-is.bpmn`, `to-be.bpmn` |
| Use Case | UML Use Case | `use-cases*.puml` |
| Состояния | UML State Machine | `*-state.puml` |
| Данные | UML Class / physical table model | `logical-model.puml`, `physical-model.puml` |
| Архитектура | C4 Context/Container, UML Component | `c4-*.puml`, `components.puml` |
| Взаимодействия | UML Sequence | `sq-*.puml` |

## Рендеринг

BPMN SVG сформированы `bpmn-to-image`, который использует `bpmn-js`. UML/C4 SVG сформированы PlantUML. Инструменты нужны только автору документации и не входят в runtime приложения.

Пример команд:

```powershell
java -jar plantuml.jar --check-syntax docs/diagrams/src
java -jar plantuml.jar --format svg --output-dir ../rendered docs/diagrams/src/*.puml
bpmn-to-image --no-title --no-footer "docs/diagrams/src/as-is.bpmn;docs/diagrams/rendered/as-is.svg"
bpmn-to-image --no-title --no-footer "docs/diagrams/src/to-be.bpmn;docs/diagrams/rendered/to-be.svg"
```

Версии, использованные для текущего рендера: PlantUML 1.2026.8 и `bpmn-to-image` 0.10.0. Локальные JRE, Chromium и package cache находятся в ignored `runtime/` либо пользовательском cache и не коммитятся.

## Проверка в CI

Workflow [`verify.yml`](../../.github/workflows/verify.yml) проверяет BPMN XML и границы flow стандартным Python-валидатором, затем выполняет полный PlantUML syntax check через проверенный JAR 1.2026.8. Chromium и SVG-рендеринг в CI не требуются: готовые изображения хранятся рядом с редактируемыми исходниками, а визуальная проверка выполняется локально после изменения модели.
