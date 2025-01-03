import os
import re
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user, login_required
)
from datetime import datetime
from collections import defaultdict

from database import (
    create_db_if_not_exists, get_or_create_bin, get_bin_info, get_bin_weight,
    update_bin_weight, list_articles_in_bin, add_article, remove_article, edit_article,
    update_bin_image, remove_bin_image, search_db, export_excel_xlsx,
    get_group_weight, get_total_articles, get_metrics, get_top_5_in, get_top_5_out,
    get_movements_in_date_range, get_articles_in_multiple_bins, get_movements_by_article_in_range
)

app=Flask(__name__)
app.secret_key="UN_SECRET_KEY_A_CHANGER"

login_manager=LoginManager()
login_manager.init_app(app)
login_manager.login_view="login"

VALID_USERNAME="admin"
VALID_PASSWORD="adminpassword"

class User(UserMixin):
    def __init__(self, user_id, username):
        self.id=user_id
        self.username=username

@login_manager.user_loader
def load_user(user_id):
    if user_id=="1":
        return User(1,VALID_USERNAME)
    return None

# Ordre E..D..C..B..A
LETTERS_ORDER=["E","D","C","B","A"]

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        username=request.form.get("username","").strip()
        password=request.form.get("password","").strip()
        if username==VALID_USERNAME and password==VALID_PASSWORD:
            user=User(1,username)
            login_user(user)
            flash("Connecté avec succès.","success")
            return redirect(url_for("index"))
        else:
            flash("Identifiants invalides.","danger")
            return redirect(url_for("login"))
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Déconnecté.","info")
    return redirect(url_for("login"))

@app.route("/")
@login_required
def index():
    """
    Page d'accueil : E..D..C..B..A, 8 bins par zone.
    Responsive (Bootstrap), on affiche tout sur une page.
    """
    lines_data=[]
    for letter in LETTERS_ORDER:
        row_bins=[]
        for num in range(1,9):
            bn=f"{letter}{num}"
            w=get_bin_weight(bn)
            ratio_percent=min((w/500)*100,100)
            row_bins.append({
                "bin_name":bn,
                "weight":w,
                "ratio_percent":ratio_percent
            })
        g1_w=get_group_weight(letter,1)
        g1_p=min((g1_w/2000)*100,100)
        g2_w=get_group_weight(letter,2)
        g2_p=min((g2_w/2000)*100,100)

        lines_data.append({
            "letter":letter,
            "bins":row_bins,
            "group1":{"weight":g1_w,"ratio_percent":g1_p},
            "group2":{"weight":g2_w,"ratio_percent":g2_p}
        })

    return render_template("index.html", lines_data=lines_data)

@app.route("/search")
@login_required
def search():
    query=request.args.get("q","").strip()
    if not query:
        flash("Veuillez saisir un texte pour la recherche.","warning")
        return redirect(url_for("index"))

    stype,val=search_db(query)
    if stype=="BIN":
        return redirect(url_for("show_bin", bin_name=val))
    else:
        articles=val
        if not articles:
            flash("Aucun article trouvé.","info")
            return redirect(url_for("index"))
        # Grouper par code => bins
        from database import get_bin_info
        code_bins=defaultdict(set)
        for (aid,bid,code,ref,log) in articles:
            binfo=get_bin_info(bid)
            if binfo:
                code_bins[code].add(binfo[1])
        results=[]
        for cde,bnset in code_bins.items():
            results.append((cde, sorted(list(bnset))))
        if len(results)==1:
            (u_code,bnlist)=results[0]
            if len(bnlist)==1:
                return redirect(url_for("show_bin", bin_name=bnlist[0]))

        return render_template("search_results.html",
                               query=query,
                               results_list=results)

@app.route("/bin/<bin_name>")
@login_required
def show_bin(bin_name):
    bin_id=get_or_create_bin(bin_name)
    binfo=get_bin_info(bin_id)
    if not binfo:
        flash("Bin introuvable.","danger")
        return redirect(url_for("index"))

    articles=list_articles_in_bin(bin_id)
    weight=binfo[2]
    ratio_percent=min((weight/500)*100,100)

    return render_template("bin_detail.html",
                           bin_info=binfo,
                           articles=articles,
                           ratio_percent=ratio_percent)

@app.route("/bin/<bin_name>/add_article", methods=["POST"])
@login_required
def bin_add_article_route(bin_name):
    bin_id=get_or_create_bin(bin_name)
    code=request.form.get("code","").strip()
    if not code:
        flash("Le code (Article) est obligatoire.","danger")
        return redirect(url_for("show_bin", bin_name=bin_name))

    reference=request.form.get("reference","").strip()
    login_field=request.form.get("login","").strip()
    qty_str=request.form.get("quantity","1").strip()
    try:
        qty=int(qty_str)
        if qty<=0:
            qty=1
    except:
        qty=1

    # verif si c'est le premier article => bin.weight=?
    # On ne force pas auto, on peut juste flash un message si weight=0 
    # (l'utilisateur doit lui-même maj le poids)
    articles_existing = list_articles_in_bin(bin_id)
    if not articles_existing:
        binfo=get_bin_info(bin_id)
        if binfo[2]==0:
            flash("ATTENTION : bin vide => pensez à définir un poids pour ce bin!", "warning")

    add_article(bin_id, code, reference, login_field, qty)
    flash(f"Article (code={code}, qty={qty}) ajouté avec succès.","success")
    return redirect(url_for("show_bin", bin_name=bin_name))

@app.route("/bin/<bin_name>/update_weight", methods=["POST"])
@login_required
def bin_update_weight_route(bin_name):
    bin_id=get_or_create_bin(bin_name)
    new_w_str=request.form.get("new_weight","0").strip()
    try:
        new_w=float(new_w_str)
    except:
        new_w=0

    # si new_w>500 => blocage
    if new_w>500:
        flash("ALERTE : Ce bin dépasse 500 kg ! Mise à jour bloquée.","warning")
        return redirect(url_for("show_bin", bin_name=bin_name))

    ok,msg=update_bin_weight(bin_id,new_w)
    if not ok:
        flash(msg,"danger")
        return redirect(url_for("show_bin", bin_name=bin_name))

    # check si groupe>2000
    letter=bin_name[0]
    num_part=bin_name[1:]
    try:
        num=int(num_part)
    except:
        num=1
    grp=1 if num<=4 else 2
    gw=get_group_weight(letter,grp)
    if gw>2000:
        update_bin_weight(bin_id,0)
        flash(f"ALERTE : Le groupe {letter}{'(1..4)' if grp==1 else '(5..8)'} dépasse 2000 kg !","warning")
    else:
        flash("Poids mis à jour.","success")

    return redirect(url_for("show_bin", bin_name=bin_name))

@app.route("/article/<int:article_id>/remove")
@login_required
def remove_article_route(article_id):
    remove_article(article_id)
    flash(f"Article ID={article_id} supprimé.","info")
    return redirect(url_for("index"))

@app.route("/article/<int:article_id>/edit", methods=["GET","POST"])
@login_required
def edit_article_route(article_id):
    from database import get_db_connection, get_bin_info
    conn=get_db_connection()
    c=conn.cursor()
    c.execute("SELECT bin_id, code, reference, login, quantity FROM Articles WHERE id=?", (article_id,))
    row=c.fetchone()
    conn.close()
    if not row:
        flash("Article introuvable.","danger")
        return redirect(url_for("index"))

    bin_id, code, old_ref, old_login, old_qty = row[0], row[1], row[2], row[3], row[4]
    binfo=get_bin_info(bin_id)
    bin_name=binfo[1] if binfo else "??"

    if request.method=="POST":
        new_ref=request.form.get("reference","").strip()
        new_login=request.form.get("login","").strip()
        qty_s=request.form.get("quantity", str(old_qty)).strip()
        try:
            new_qty=int(qty_s)
            if new_qty<=0:
                new_qty=1
        except:
            new_qty=1

        edit_article(article_id,new_ref,new_qty,new_login)
        flash(f"Article {code} modifié avec succès.","success")
        return redirect(url_for("show_bin", bin_name=bin_name))

    return render_template("article_edit.html",
                           article_id=article_id,
                           bin_name=bin_name,
                           code=code,
                           old_ref=old_ref or "",
                           old_login=old_login or "",
                           old_qty=old_qty)

@app.route("/bin/<bin_name>/upload_image", methods=["POST"])
@login_required
def bin_upload_image_route(bin_name):
    bin_id=get_or_create_bin(bin_name)
    f=request.files.get("bin_image")
    if f and f.filename:
        folder=os.path.join("static","images")
        os.makedirs(folder, exist_ok=True)
        _, ext=os.path.splitext(f.filename)
        ts=0
        try:
            ts=os.path.getmtime(f.filename)
        except:
            pass
        newfile=f"{bin_name}_{int(ts)}{ext}"
        path=os.path.join(folder,newfile)
        f.save(path)
        update_bin_image(bin_id,path)
        flash("Image mise à jour.","success")
    else:
        flash("Aucune image sélectionnée.","warning")
    return redirect(url_for("show_bin", bin_name=bin_name))

@app.route("/bin/<bin_name>/remove_image")
@login_required
def bin_remove_image_route(bin_name):
    bin_id=get_or_create_bin(bin_name)
    remove_bin_image(bin_id)
    flash("Image supprimée.","info")
    return redirect(url_for("show_bin", bin_name=bin_name))

@app.route("/bin/<bin_name>/full_image")
@login_required
def bin_full_image(bin_name):
    bin_id=get_or_create_bin(bin_name)
    binfo=get_bin_info(bin_id)
    if not binfo:
        flash("Bin introuvable ou image introuvable.","danger")
        return redirect(url_for("index"))
    image_path=binfo[3]
    if image_path:
        parts=image_path.split("static"+os.sep, maxsplit=1)
        if len(parts)==2:
            rel_path=re.sub(r"\\","/", parts[1])
        else:
            rel_path=re.sub(r"\\","/", image_path)
    else:
        rel_path=None
    return render_template("bin_image_full.html",
                           bin_name=bin_name,
                           image_path=image_path,
                           rel_path=rel_path)

@app.route("/dashboard", methods=["GET","POST"])
@login_required
def dashboard():
    total_articles=get_total_articles()
    articles_in,articles_out=get_metrics()
    top5_in_list=get_top_5_in()
    top5_out_list=get_top_5_out()

    def_start=datetime.now().strftime("%Y-%m-%d")
    def_end=def_start
    start_date=request.form.get("start_date", def_start)
    end_date=request.form.get("end_date", def_end)

    moves=get_movements_in_date_range(start_date,end_date)
    flux_by_day={}
    for mv in moves:
        # mv = (id, article_id, bin_id, action, qty_change, date_time)
        dt=mv[5][:10]
        flux_by_day[dt]=flux_by_day.get(dt,0)+1
    sorted_days=sorted(flux_by_day.keys())
    chart_labels=sorted_days
    chart_values=[flux_by_day[d] for d in sorted_days]

    usage_list=get_movements_by_article_in_range(start_date,end_date)
    usage_labels=[u[0] for u in usage_list]
    usage_values=[u[1] for u in usage_list]

    duplicates=get_articles_in_multiple_bins()

    return render_template("dashboard.html",
                           total_articles=total_articles,
                           articles_in=articles_in,
                           articles_out=articles_out,
                           top5_in_list=top5_in_list,
                           top5_out_list=top5_out_list,
                           start_date=start_date,
                           end_date=end_date,
                           chart_labels=chart_labels,
                           chart_values=chart_values,
                           usage_labels=usage_labels,
                           usage_values=usage_values,
                           duplicates=duplicates)

@app.route("/export_excel")
@login_required
def export_excel():
    path=export_excel_xlsx()
    return send_file(path, as_attachment=True)

if __name__=="__main__":
    create_db_if_not_exists()
    app.run(debug=True, host="0.0.0.0", port=5000)
