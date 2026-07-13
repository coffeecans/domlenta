# DIY Дом Лента — массовая AI-обработка фото товаров

Веб-приложение на Flask для пакетной загрузки фотографий товаров интернет-магазина DIY Дом Лента. Один исходный файл считается одним SKU. Пользователь задаёт цвет каталожного фона и промт контекстной сцены, а приложение удаляет фон товара через open-source AI-модель и формирует ZIP-архив с папками по SKU.

## Возможности

- массовая загрузка изображений из папки через веб-интерфейс;
- обязательный ввод только двух параметров партии: цвет каталожного фона и промт контекстной сцены;
- AI-удаление исходного фона товара через `rembg` / IS-Net;
- генерация контекстной сцены через open-source Diffusers / Stable Diffusion и сохранение вырезанного товара как главного объекта;
- фиксированные имена результатов `<sku>_ai_1.png` и `<sku>_ai_2.png`;
- генерация отдельных папок по SKU;
- скачивание результата ZIP-архивом для последующей загрузки в PIM;
- временное хранение результатов без обязательного постоянного архива.


## Структура результата

Для каждого исходного файла создаётся отдельная папка SKU:

```text
902884_1/
├── 902884_1.png
├── 902884_1_ai_1.png
└── 902884_1_ai_2.png
```

- `*_ai_1.png` — товар с AI-удалением исходного фона на выбранном каталожном фоне;
- `*_ai_2.png` — товар как главный объект в AI-сгенерированной контекстной сцене по промту.

## Локальный запуск

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
flask --app app run --debug
```

Откройте http://127.0.0.1:5000.

Для проверки production-команды локально можно запустить:

```bash
gunicorn app:app
```

## Деплой на Render.com

В корне репозитория уже есть файлы для Render, а код приложения лежит в `dom_lenta_photo_app/`:

- `dom_lenta_photo_app/requirements.txt` — зависимости приложения, включая `gunicorn` для production-запуска;
- `render.yaml` — Blueprint-конфигурация Web Service;
- `Procfile` — альтернативная команда запуска `web: gunicorn app:app`;
- `.python-version` и `PYTHON_VERSION` в `render.yaml` — фиксированная версия Python для повторяемой сборки;
- `runtime.txt` — дополнительная совместимость для Heroku-style окружений.

### Вариант 1: Blueprint через `render.yaml`

1. Запушьте текущую ветку в GitHub/GitLab-репозиторий.
2. В Render откройте **Dashboard → New → Blueprint**.
3. Подключите репозиторий с этим проектом.
4. Render прочитает `render.yaml`, установит зависимости командой `pip install -r dom_lenta_photo_app/requirements.txt` и запустит приложение командой `gunicorn --chdir dom_lenta_photo_app app:app`.
5. После успешной сборки приложение будет доступно по URL вида `https://<service-name>.onrender.com`.

### Вариант 2: ручной Web Service

1. В Render откройте **Dashboard → New → Web Service**.
2. Подключите репозиторий.
3. Укажите параметры:
   - **Language**: `Python 3`;
   - **Build Command**: `pip install -r dom_lenta_photo_app/requirements.txt`;
   - **Start Command**: `gunicorn --chdir dom_lenta_photo_app app:app`;
   - **Health Check Path**: `/healthz`.
4. В разделе **Environment** добавьте `SECRET_KEY` или используйте автогенерацию из `render.yaml` при Blueprint-деплое.
5. Нажмите **Create Web Service** и дождитесь окончания сборки.

> Важно: приложение хранит исходники и ZIP-архивы во временной файловой системе сервиса. Это подходит для сценария «обработал → скачал», но не для долгосрочного хранения. Для постоянного хранения нужно подключить Render Disk или внешний object storage.
>
> AI-зависимости (`rembg`, `diffusers`, `torch`) тяжёлые. Для production-качества контекстных сцен лучше использовать GPU-инстанс или отдельный worker с GPU; бесплатный Render-план может быть слишком слабым для генерации Stable Diffusion.


## Render build troubleshooting

Если Render пишет `No matching distribution found for rembg==...`, значит выбранная версия `rembg` не поддерживает Python-версию build image. В `requirements.txt` используется более новая версия `rembg==2.0.76`, а в корне репозитория добавлен `.python-version` с Python `3.12.13`, чтобы Render не брал дефолтный Python 3.14.x, несовместимый с частью AI-зависимостей.
